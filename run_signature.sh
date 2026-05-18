#!/bin/bash
# run_signature.sh - Interactive wrapper for add_signature_to_pdf
# Automatically uses venv python and runs with default/interactive parameters

set -e

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default values
DEFAULT_INPUT="./input"
DEFAULT_OUTPUT="./output"
DEFAULT_NAME="hulyatun maskunah"
DEFAULT_SIGNATURE="./hulya_signature.jpeg"

# Function to prompt with default value
prompt_with_default() {
    local prompt_text="$1"
    local default_value="$2"
    
    if [ -t 0 ]; then
        read -p "$prompt_text [$default_value]: " user_input
        if [ -z "$user_input" ]; then
            echo "$default_value"
        else
            echo "$user_input"
        fi
    else
        echo "$default_value"
    fi
}

# Interactive mode if no arguments provided or -Interactive flag
INTERACTIVE=0
if [ $# -eq 0 ] || [ "$1" = "-Interactive" ]; then
    INTERACTIVE=1
fi

if [ "$INTERACTIVE" -eq 1 ]; then
    echo "=== Signature PDF Tool ==="
    echo ""
    
    INPUT_DIR=$(prompt_with_default "Input folder path" "$DEFAULT_INPUT")
    OUTPUT_DIR=$(prompt_with_default "Output folder path" "$DEFAULT_OUTPUT")
    NAME=$(prompt_with_default "Signature name" "$DEFAULT_NAME")
    SIGNATURE=$(prompt_with_default "Signature image path" "$DEFAULT_SIGNATURE")
else
    # Use provided arguments or defaults
    INPUT_DIR="${1:-$DEFAULT_INPUT}"
    OUTPUT_DIR="${2:-$DEFAULT_OUTPUT}"
    NAME="${3:-$DEFAULT_NAME}"
    SIGNATURE="${4:-$DEFAULT_SIGNATURE}"
fi

echo ""
echo "Running with parameters:"
echo "  Input:      $INPUT_DIR"
echo "  Output:     $OUTPUT_DIR"
echo "  Name:       $NAME"
echo "  Signature:  $SIGNATURE"
echo ""

# Use python directly from venv
PYTHON_EXE="$SCRIPT_DIR/.venv/bin/python"
if [ ! -f "$PYTHON_EXE" ]; then
    echo "Warning: Python not found in venv at $PYTHON_EXE"
    echo "Falling back to system python"
    PYTHON_EXE="python3"
fi

# Run the Python script
"$PYTHON_EXE" "$SCRIPT_DIR/main.py" \
    --input "$INPUT_DIR" \
    --output "$OUTPUT_DIR" \
    --name "$NAME" \
    --signature "$SIGNATURE"