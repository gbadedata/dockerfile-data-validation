"""dvgate: validation gates for datasets, schemas and model artefacts in Docker builds."""

__version__ = "1.0.0"

# Exit codes shared by every command, so a build or scheduler can tell a
# failed gate from a broken configuration.
EXIT_OK = 0
EXIT_CONFIG_ERROR = 1
EXIT_GATE_FAILED = 2
