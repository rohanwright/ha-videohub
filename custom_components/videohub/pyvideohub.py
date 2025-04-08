"""BlackMagic Videohub device implementation."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

_LOGGER = logging.getLogger(__name__)
SERVER_RECONNECT_DELAY = 30


class BlackmagicVideohub(asyncio.Protocol):
    """Blackmagic Videohub device implementation using asyncio Protocol."""
    
    def __init__(self, cmdServer: str, cmdServerPort: int, loop: Optional[asyncio.AbstractEventLoop] = None):
        """Initialize the Blackmagic Videohub device."""
        # Connection details
        self._cmdServer = cmdServer
        self._cmdServerPort = cmdServerPort
        self._transport = None
        self._connected = False
        self._connecting = False
        self._error_message = None
        self._allow_reconnect = True  # Flag to control reconnection behavior
        
        # Buffer for incoming data
        self._buffer = ""
        
        # State data
        self.initialised = False
        # Store inputs with zero-based IDs and their labels
        self.inputs: Dict[int, Dict[str, Any]] = {}  # {zero_based_id: {"label": label, "ha_name": "Input X - Label"}}
        # Store outputs with zero-based IDs, their labels and current input
        self.outputs: Dict[int, Dict[str, Any]] = {}  # {zero_based_id: {"label": label, "input": input_zero_id, "ha_entity": "Entity"}}
        
        # Callback handling
        self._update_callbacks: List[Callable] = []
        
        # Device information
        self.model_name: Optional[str] = None
        self.video_inputs_count: int = 0
        self.video_outputs_count: int = 0
        self.device_present: bool = False
        
        # Track which data blocks we've received
        self._received_blocks: Set[str] = set()

        # Get event loop
        self._event_loop = loop if loop else asyncio.get_event_loop()
    
    # --- Protocol implementation methods ---
    
    def connection_made(self, transport):
        """asyncio callback for a successful connection."""
        _LOGGER.info("Connected to Blackmagic Videohub at %s:%s", 
                     self._cmdServer, self._cmdServerPort)
        self._transport = transport
        self._connected = True
        self._connecting = False
        self._error_message = None
        self._buffer = ""  # Clear buffer on new connection
        
        # Schedule a task to request initial data
        asyncio.ensure_future(self._request_initial_data())
        # Start the keep_alive task
        asyncio.ensure_future(self.keep_alive())

    def data_received(self, data):
        """asyncio callback when data is received on the socket."""
        if not data:
            return
            
        # Add received data to buffer and process
        data_str = data.decode("utf-8", errors="replace")
        _LOGGER.debug("Raw data received: %s", data_str)  # Log raw data for debugging
        _LOGGER.debug("Received data (%d bytes)", len(data))
        
        # Append to buffer
        self._buffer += data_str
        
        # Process complete blocks from buffer
        self._process_buffer()
            
    def _process_buffer(self):
        """Process the data buffer for complete blocks."""
        # Normalize line endings in buffer
        self._buffer = self._buffer.replace('\r\n', '\n').replace('\r', '\n')

        # Find complete blocks that end with double newlines
        blocks = []
        remaining_buffer = ""
        
        # Split the buffer by double newlines to identify complete blocks
        parts = self._buffer.split("\n\n")
        
        # Process all parts except possibly the last one
        for i, part in enumerate(parts):
            if i == len(parts) - 1 and not self._buffer.endswith("\n\n"):
                # This is the last part and doesn't end with newlines, so keep it for next time
                remaining_buffer = part
                continue
                
            # Skip empty parts
            if not part.strip():
                continue
                
            # Process this complete block
            lines = part.strip().split("\n")
            if not lines:
                continue
                
            # The first line should be the header with a colon
            if ":" in lines[0]:
                block_type = lines[0].split(":", 1)[0]
                # Content is everything after the header
                content_lines = lines[1:] if len(lines) > 1 else []
                blocks.append((block_type, content_lines))
                _LOGGER.debug("Found complete block: %s with %d lines", block_type, len(content_lines))
        
        # Update buffer with remaining content
        self._buffer = remaining_buffer
        
        # Process all complete blocks
        for block_type, block_lines in blocks:
            _LOGGER.debug("Processing complete block: %s with %d lines", block_type, len(block_lines))
            self._process_block(block_type, block_lines)
            
    def connection_lost(self, exc):
        """asyncio callback for a lost TCP connection."""
        self._connected = False
        
        if exc:
            _LOGGER.error("Connection lost: %s", str(exc))
        else:
            _LOGGER.info("Connection closed")
        
        # Notify clients about the disconnection
        self._send_update_callback()
        
        # Start reconnection process only if allowed
        if hasattr(self, '_event_loop') and not self._connecting and self._allow_reconnect:
            asyncio.ensure_future(self.reconnect())
        else:
            _LOGGER.debug("Not attempting reconnection as reconnection is disabled")
    
    # --- Data processing methods ---
    
    def _process_block(self, block_name: str, lines: List[str]) -> None:
        """Process a data block based on its name."""
        _LOGGER.debug("Processing block: %s (%d lines)", block_name, len(lines))
        
        # Track that we've received this block type
        self._received_blocks.add(block_name)
        
        if block_name == "PROTOCOL PREAMBLE":
            for line in lines:
                if line.startswith("Version: "):
                    protocol_version = line.split("Version: ")[1]
                    _LOGGER.info("Device protocol version: %s", protocol_version)
                    # Mark as initialized since we've received protocol info
                    self.initialised = True
        
        elif block_name == "VIDEOHUB DEVICE":
            self._process_device_info(lines)
            # Mark as initialized after processing device info
            self.initialised = True
            
        elif block_name == "INPUT LABELS":
            self._process_input_labels(lines)
            # Mark as initialized after processing labels
            self.initialised = True
            
        elif block_name == "OUTPUT LABELS":
            self._process_output_labels(lines)
            # Mark as initialized after processing labels
            self.initialised = True
            
        elif block_name == "VIDEO OUTPUT ROUTING":
            self._process_output_routing(lines)
            # Mark as initialized after processing routing
            self.initialised = True
            
        elif block_name == "ACK":
            _LOGGER.debug("Command acknowledged by device")
            
        elif block_name == "NAK":
            error_details = lines[0] if lines else "Unknown error"
            _LOGGER.warning("Command rejected: %s", error_details)
            self._error_message = f"Command rejected: %s" % error_details
    
    def _process_device_info(self, lines: List[str]) -> None:
        """Process device information block."""
        for line in lines:
            if line.startswith("Device present: "):
                status = line.split("Device present: ")[1].strip()
                self.device_present = (status == "true")
                if not self.device_present:
                    _LOGGER.warning("Device reports not present: %s", status)
                    if status == "needs_update":
                        self._error_message = "Device firmware needs update"
            
            elif line.startswith("Model name: "):
                self.model_name = line.split("Model name: ")[1].strip()
                _LOGGER.info("Model: %s", self.model_name)
                
            elif line.startswith("Video inputs: "):
                try:
                    self.video_inputs_count = int(line.split("Video inputs: ")[1].strip())
                    _LOGGER.info("Inputs: %d", self.video_inputs_count)
                except ValueError:
                    _LOGGER.warning("Invalid input count: %s", line)
                    
            elif line.startswith("Video outputs: "):
                try:
                    self.video_outputs_count = int(line.split("Video outputs: ")[1].strip())
                    _LOGGER.info("Outputs: %d", self.video_outputs_count)
                except ValueError:
                    _LOGGER.warning("Invalid output count: %s", line)
    
    def _process_input_labels(self, lines: List[str]) -> None:
        """Process input labels block."""
        updated_input_ids = []
        
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    input_id = int(parts[0])  # This is zero-based from protocol
                    label = parts[1].strip()
                    
                    # Check if this input exists and label has changed
                    label_changed = (input_id not in self.inputs or 
                                    self.inputs[input_id].get("label") != label)
                    
                    # Store with the zero-based ID
                    display_id = input_id + 1  # Convert to 1-based for display
                    ha_name = f"Input {display_id} - {label}"
                    
                    self.inputs[input_id] = {
                        "label": label,
                        "ha_name": ha_name,
                        "display_id": display_id
                    }
                    
                    _LOGGER.debug("Input %d: %s", display_id, label)
                    
                    # If label changed, add to list of updated inputs
                    if label_changed:
                        updated_input_ids.append(input_id)
                        
                except ValueError:
                    _LOGGER.warning("Invalid input label line: %s", line)
        
        # If any input labels were updated, notify callbacks
        if updated_input_ids:
            _LOGGER.info("Input labels updated, notifying callbacks")
            self._send_update_callback()
    
    def _process_output_labels(self, lines: List[str]) -> None:
        """Process output labels block."""
        for line in lines:
            parts = line.split(" ", 1)
            if len(parts) == 2:
                try:
                    output_id = int(parts[0])  # This is zero-based from protocol
                    label = parts[1].strip()
                    
                    # Get or create output entry
                    if output_id not in self.outputs:
                        self.outputs[output_id] = {}
                    
                    # Store with the zero-based ID
                    display_id = output_id + 1  # Convert to 1-based for display
                    
                    # Update label
                    self.outputs[output_id]["label"] = label
                    self.outputs[output_id]["display_id"] = display_id
                    
                    # Use existing input if we have it, otherwise set to 0
                    if "input" not in self.outputs[output_id]:
                        self.outputs[output_id]["input"] = 0
                    
                    _LOGGER.debug("Output %d: %s", display_id, label)
                    
                except ValueError:
                    _LOGGER.warning("Invalid output label line: %s", line)

    def _process_output_routing(self, lines: List[str]) -> None:
        """Process output routing block."""
        for line in lines:
            parts = line.split(" ")
            if len(parts) == 2:
                try:
                    output_id = int(parts[0])  # Zero-based from protocol
                    input_id = int(parts[1])   # Zero-based from protocol
                    
                    # Create output if it doesn't exist
                    if output_id not in self.outputs:
                        self.outputs[output_id] = {
                            "label": f"Output {output_id + 1:03d}",  # Default name with zero padding
                            "display_id": output_id + 1
                        }
                    
                    # Update the input assignment
                    self.outputs[output_id]["input"] = input_id
                    
                    _LOGGER.debug("Output %d routed to input %d", output_id + 1, input_id + 1)
                    
                    # Notify about this specific output change
                    self._send_update_callback(output_id)
                    
                except ValueError:
                    _LOGGER.warning("Invalid routing line: %s", line)
    
    # --- Connection management methods ---
    
    def start(self) -> None:
        """Start connection to the Videohub device."""
        _LOGGER.info("Connecting to Videohub at %s:%s", self._cmdServer, self._cmdServerPort)
        self._connecting = True
        self._allow_reconnect = True  # Reset reconnect flag when explicitly starting
        
        try:
            coro = self._event_loop.create_connection(
                lambda: self, self._cmdServer, self._cmdServerPort
            )
            asyncio.ensure_future(coro)
        except Exception as ex:
            _LOGGER.error("Failed to start connection: %s", str(ex))
            self._connecting = False
            self._error_message = f"Connection failed: {str(ex)}"

    def stop(self, allow_reconnect: bool = True) -> None:
        """Stop connection to the Videohub device.
        
        Args:
            allow_reconnect: Whether to allow automatic reconnection attempts
        """
        _LOGGER.info("Stopping connection to Videohub (allow_reconnect=%s)", allow_reconnect)
        
        # Set flags to control connection behavior
        self._connected = False
        self._connecting = False
        self._allow_reconnect = allow_reconnect
        
        # Close transport if connected
        if self._transport:
            try:
                self._transport.close()
            except Exception as ex:
                _LOGGER.error("Error closing transport: %s", str(ex))
        else:
            _LOGGER.debug("No transport to close")
        
        # Clear callbacks only if not allowing reconnection
        if not allow_reconnect:
            self._update_callbacks = []

    async def reconnect(self) -> None:
        """Attempt to reconnect to the device."""
        if self._connecting or not self._allow_reconnect:
            _LOGGER.debug("Reconnection skipped: already in progress or disabled")
            return
            
        attempt = 0
        max_attempts = 5
        
        while not self._connected and not self._connecting and self._allow_reconnect:
            attempt += 1
            _LOGGER.info("Reconnection attempt %d of %d", attempt, max_attempts)
            
            self._connecting = True  # Mark as connecting before actually starting
            try:
                coro = self._event_loop.create_connection(
                    lambda: self, self._cmdServer, self._cmdServerPort
                )
                asyncio.ensure_future(coro)
            except Exception as ex:
                _LOGGER.error("Failed reconnection: %s", str(ex))
                self._connecting = False
            
            await asyncio.sleep(5)  # Wait to see if connection succeeded
            
            if self._connected:
                _LOGGER.info("Successfully reconnected")
                break
                
            # Reset connecting flag if still not connected
            self._connecting = False
                
            # Increase delay between attempts
            backoff_delay = min(SERVER_RECONNECT_DELAY * (attempt * 0.5), 60)
            _LOGGER.info("Next reconnection attempt in %.1f seconds", backoff_delay)
            await asyncio.sleep(backoff_delay)
            
            # Reset attempt counter after max attempts
            if attempt >= max_attempts:
                _LOGGER.warning("Maximum reconnection attempts reached, taking a longer break")
                await asyncio.sleep(SERVER_RECONNECT_DELAY)
                attempt = 0
    
    async def keep_alive(self) -> None:
        """Send periodic keep-alive pings to the device."""
        while self._connected:
            try:
                await asyncio.sleep(60)  # Send ping every minute
                if self._connected:
                    _LOGGER.debug("Sending keep-alive ping")
                    await self.send_command("PING:\n\n")
            except Exception as ex:
                _LOGGER.error("Keep-alive error: %s", str(ex))
                break
    
    async def _request_initial_data(self) -> None:
        """Request initial data from the device after connection."""
        if not self._connected:
            return
            
        _LOGGER.info("Requesting initial data from Videohub")
        
        try:
            # Wait a moment to see what data the device sends automatically
            await asyncio.sleep(1)
            
            # Only request data blocks we haven't received yet
            required_blocks = [
                "VIDEOHUB DEVICE", 
                "INPUT LABELS", 
                "OUTPUT LABELS", 
                "VIDEO OUTPUT ROUTING"
            ]
            
            for block_name in required_blocks:
                if block_name not in self._received_blocks:
                    _LOGGER.info("Requesting missing data: %s", block_name)
                    await self.request_status_dump(block_name)
                    await asyncio.sleep(0.5)
                else:
                    _LOGGER.debug("Skipping already received block: %s", block_name)
            
            # Check if we've received all required data
            missing_blocks = [block for block in required_blocks 
                             if block not in self._received_blocks]
            if missing_blocks:
                _LOGGER.warning("Missing data blocks after initialization: %s", 
                               ", ".join(missing_blocks))
            else:
                _LOGGER.info("All required data blocks received successfully")
                
        except Exception as ex:
            _LOGGER.error("Error requesting initial data: %s", str(ex))
    
    # --- Command methods ---
    
    async def send_command(self, command: str) -> None:
        """Send a raw command to the device."""
        if not self._connected:
            _LOGGER.warning("Cannot send command - not connected")
            return
            
        try:
            _LOGGER.debug("Sending: %s", command.replace("\n", "\\n"))
            self._transport.write(command.encode("ascii"))
        except Exception as ex:
            _LOGGER.error("Failed to send command: %s", str(ex))
            if self._connected:
                _LOGGER.warning("Connection may be in a bad state - closing")
                self._connected = False
                self._transport.close()
    
    async def request_status_dump(self, block_name: str) -> None:
        """Request a specific status block from the device."""
        if not self._connected:
            _LOGGER.warning("Cannot request status - not connected")
            return
            
        _LOGGER.debug("Requesting: %s", block_name)
        await self.send_command(f"{block_name}:\n\n")
    
    def set_input(self, output_id: int, input_id: int) -> bool:
        """Set input for an output.
        
        Args:
            output_id: One-based output ID (as shown to user)
            input_id: One-based input ID (as shown to user)
            
        Returns:
            bool: True if command was sent successfully
        """
        if not self._connected:
            _LOGGER.warning("Cannot set input - not connected")
            return False
            
        # Convert to zero-based IDs for the protocol
        zero_output_id = output_id - 1
        zero_input_id = input_id - 1
        
        _LOGGER.info("Setting output %d to input %d (protocol: %d → %d)", 
                      output_id, input_id, zero_output_id, zero_input_id)
        
        try:
            command = f"VIDEO OUTPUT ROUTING:\n{zero_output_id} {zero_input_id}\n\n"
            self._transport.write(command.encode("ascii"))
            return True
        except Exception as ex:
            _LOGGER.error("Failed to set input: %s", str(ex))
            return False
    
    def set_input_by_name(self, output_id: int, input_name: str) -> bool:
        """Set input by name rather than ID.
        
        Args:
            output_id: One-based output ID
            input_name: The input identifier (can be an input number as string, 
                        or the full name like "Input 2 - Camera")
            
        Returns:
            bool: True if command was sent successfully
        """
        # First check if input_name is just a number
        if isinstance(input_name, str) and input_name.isdigit():
            try:
                input_id = int(input_name)
                return self.set_input(output_id, input_id)
            except ValueError:
                pass
                
        # Extract input ID from the full name format
        match = re.match(r"Input (\d+) -", input_name)
        if match:
            try:
                input_id = int(match.group(1))
                return self.set_input(output_id, input_id)
            except ValueError:
                pass
                
        # If extraction failed, try to find the input by name
        for input_zero_id, input_data in self.inputs.items():
            if input_data["ha_name"] == input_name:
                return self.set_input(output_id, input_zero_id + 1)
                
        _LOGGER.warning("Could not find input: %s", input_name)
        return False
    
    def set_output_label(self, output_id: int, label: str) -> bool:
        """Set a label for an output.
        
        Args:
            output_id: One-based output ID
            label: New label for the output
            
        Returns:
            bool: True if command was sent successfully
        """
        if not self._connected:
            return False
            
        # Convert to zero-based ID for the protocol
        zero_output_id = output_id - 1
        
        try:
            command = f"OUTPUT LABELS:\n{zero_output_id} {label}\n\n"
            self._transport.write(command.encode("ascii"))
            return True
        except Exception as ex:
            _LOGGER.error("Failed to set output label: %s", str(ex))
            return False
    
    def set_input_label(self, input_id: int, label: str) -> bool:
        """Set a label for an input.
        
        Args:
            input_id: One-based input ID
            label: New label for the input
            
        Returns:
            bool: True if command was sent successfully
        """
        if not self._connected:
            return False
            
        # Convert to zero-based ID for the protocol
        zero_input_id = input_id - 1
        
        try:
            command = f"INPUT LABELS:\n{zero_input_id} {label}\n\n"
            self._transport.write(command.encode("ascii"))
            return True
        except Exception as ex:
            _LOGGER.error("Failed to set input label: %s", str(ex))
            return False
    
    # --- Data access methods ---
    
    def get_inputs(self) -> Dict[int, Dict[str, Any]]:
        """Get all inputs with their information.
        
        Returns:
            Dictionary of {one-based ID: {"label": label, "ha_name": ha_name}}
        """
        # Convert to one-based IDs for external use
        result = {}
        for zero_id, input_data in self.inputs.items():
            one_based_id = zero_id + 1
            result[one_based_id] = input_data.copy()
        return result
    
    def get_outputs(self) -> Dict[int, Dict[str, Any]]:
        """Get all outputs with their information.
        
        Returns:
            Dictionary of {one-based ID: {"label": label, "input": one-based input ID}}
        """
        # Convert to one-based IDs for external use
        result = {}
        for zero_id, output_data in self.outputs.items():
            one_based_id = zero_id + 1
            output_copy = output_data.copy()
            
            # Convert the input to one-based as well
            if "input" in output_copy:
                output_copy["input"] = output_copy["input"] + 1
                
                # Add input label if available
                input_zero_id = output_data["input"]
                if input_zero_id in self.inputs:
                    output_copy["input_name"] = self.inputs[input_zero_id]["label"]
                else:
                    output_copy["input_name"] = f"Input {input_zero_id + 1}"
            
            result[one_based_id] = output_copy
            
        return result
    
    def get_input_name(self, input_id: int) -> str:
        """Get the name of a specific input.
        
        Args:
            input_id: One-based input ID
            
        Returns:
            String name of the input or a default name
        """
        zero_id = input_id - 1
        if zero_id in self.inputs:
            return self.inputs[zero_id]["label"]
        return f"Input {input_id}"
    
    # --- Callback methods ---
    
    def add_update_callback(self, callback: Callable) -> None:
        """Add a callback for updates."""
        if callback not in self._update_callbacks:
            self._update_callbacks.append(callback)
    
    def remove_update_callback(self, callback: Callable) -> None:
        """Remove a callback."""
        if callback in self._update_callbacks:
            self._update_callbacks.remove(callback)
    
    def _send_update_callback(self, output_id: int = None) -> None:
        """Send update to all callbacks."""
        for callback in self._update_callbacks:
            try:
                callback(output_id=output_id)
            except Exception as ex:
                _LOGGER.error("Error in callback: %s", str(ex))
    
    # --- Properties ---
    
    @property
    def connected(self) -> bool:
        """Whether the device is currently connected."""
        return self._connected
    
    @property
    def is_initialised(self) -> bool:
        """Whether the device is fully initialized."""
        return self.initialised
    
    @property
    def error_message(self) -> Optional[str]:
        """Last error message or None if no errors."""
        return self._error_message