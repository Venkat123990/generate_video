FROM runpod/pytorch:2.1.2-py3.10-cuda12.1-devel

# Install dependencies
RUN apt-get update && apt-get install -y git ffmpeg libsm6 libxext6 wget

# Install ComfyUI
RUN git clone https://github.com/comfyanonymous/ComfyUI /workspace/ComfyUI

# Install requirements
RUN pip install --upgrade pip
RUN pip install opencv-python Pillow einops transformers accelerate safetensors wget

# Copy workflow files
COPY . /workspace/

# Models directory
RUN mkdir -p /workspace/ComfyUI/models/checkpoints
RUN mkdir -p /workspace/ComfyUI/models/loras

# FL2V video model
RUN wget -O /workspace/ComfyUI/models/checkpoints/model.safetensors \
https://huggingface.co/wlsdml/FL2V/resolve/main/model.safetensors

# Set entrypoint
WORKDIR /workspace
CMD ["python3", "handler.py"]
