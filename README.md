# Blackmagic Videohub Integration for Home Assistant

Home Assistant integration for Blackmagic Videohub devices, allowing you to control your video routing matrix directly from Home Assistant.

Blackmagic provides an API to control their video switches over TCP. This integration implements a fully asynchronous interface that automatically updates Home Assistant if any changes are made to the device directly or from other clients.

Each output of your Videohub will be represented as a select entity in Home Assistant, allowing you to choose which input source is displayed on each output.

## Features

- **Source Selection**: Change which input is routed to each output
- **Multiple Devices**: Support for multiple Videohub devices in a single Home Assistant instance
- **Name Customization**: Set custom names for inputs and outputs
- **Connection Status**: Monitor connection state with the device
- **Real-time Updates**: Changes made on the device are reflected in Home Assistant
- **UI Configuration**: Easy setup through the UI without YAML editing
- **State Recovery**: Maintains state during reconnections
- **Custom Services**: Specialized services for full device control

## Installation

### HACS Installation (Recommended)
1. Make sure you have [HACS](https://hacs.xyz/) installed
2. Go to HACS → Integrations → Click the three dots in the top right corner → Custom repositories
3. Add this repository URL (`https://github.com/rohanwright/ha-videohub`) and select "Integration" as the category
4. Click "Add" and then search for "Blackmagic Videohub" in the integrations section
5. Install the integration

### Manual Installation
1. Copy the `custom_components/videohub` directory into your Home Assistant `/config/custom_components` directory
2. Restart Home Assistant

## Configuration

### Using the UI (Recommended)
1. Go to Settings → Devices & Services
2. Click "Add Integration" in the bottom right corner
3. Search for "Blackmagic Videohub" and select it
4. Enter the IP address, port (default: 9990), and name for your Videohub

### Configuration Options
After setup, you can adjust integration options by clicking on the "Configure" button:
- **Initialization Timeout**: Adjust if your device takes longer to initialize

## Services

The integration provides several services to control your Blackmagic Videohub:

### `videohub.set_input`
Sets an input for a specific output.
- **entity_id**: The output entity to configure
- **input**: The input to select (can be input number or input name)

### `videohub.set_output_label`
Sets a custom label for an output.
- **entity_id**: The output entity to rename
- **label**: The new name for the output

### `videohub.set_input_label`
Sets a custom label for an input.
- **entity_id**: Any entity from this integration (used to identify the device)
- **input**: The input number to rename
- **label**: The new name for the input

## Entities

### Select Entities
Each output on your Videohub will appear as a select entity, allowing you to choose which input is displayed on that output.

### Sensor Entities
- **Connection Status**: Shows if the device is connected, disconnected, or initializing

## Troubleshooting

- **Connection Issues**: Ensure the device is powered on and the IP address is correct
- **No Outputs Found**: Check if the device model is supported and firmware is up to date
- **Initialization Timeout**: Try increasing the initialization timeout in the configuration options

## Supported Devices

Tested with:
- Blackmagic Smart Videohub 40x40

Should work with other Blackmagic Design Videohub models that support the Videohub protocol over Ethernet.

## License

This project is licensed under the MIT License - see the LICENSE file for details.
