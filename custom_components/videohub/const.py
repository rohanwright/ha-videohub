"""Constants for the Blackmagic Videohub integration."""

DOMAIN = "videohub"
DEFAULT_PORT = 9990
DEFAULT_NAME = "Blackmagic Videohub"
DEFAULT_RECONNECT_DELAY = 30
DEFAULT_KEEP_ALIVE_INTERVAL = 120

# Configuration related
CONF_RECONNECT_ATTEMPTS = "reconnect_attempts"
CONF_RECONNECT_DELAY = "reconnect_delay"
CONF_INIT_TIMEOUT = "init_timeout"

# Data related
DATA_VIDEOHUB = "videohub"
DATA_COORDINATOR = "coordinator"

# Entity attributes
ATTR_MODEL_NAME = "model_name"
ATTR_VIDEO_INPUTS = "video_inputs"
ATTR_VIDEO_OUTPUTS = "video_outputs"

# Status states
STATE_CONNECTED = "connected"
STATE_DISCONNECTED = "disconnected"
STATE_INITIALIZING = "initializing"

# Services
SERVICE_SET_INPUT = "set_input"
SERVICE_SET_OUTPUT_LABEL = "set_output_label"
SERVICE_SET_INPUT_LABEL = "set_input_label"
