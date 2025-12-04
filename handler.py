import runpod
from runpod.serverless.utils import rp_upload
import os
import websocket
import base64
import json
import uuid
import logging
import urllib.request
import urllib.parse
import binascii
import subprocess
import time
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

server_address = os.getenv('SERVER_ADDRESS', '127.0.0.1')
client_id = str(uuid.uuid4())


def to_nearest_multiple_of_16(value):
    try:
        numeric_value = float(value)
    except Exception:
        raise Exception(f"width/height is not a number: {value}")
    adjusted = int(round(numeric_value / 16.0) * 16)
    if adjusted < 16:
        adjusted = 16
    return adjusted


def process_input(input_data, temp_dir, output_filename, input_type):
    if input_type == "path":
        logger.info(f"📁 Using image path: {input_data}")
        return input_data

    elif input_type == "url":
        logger.info(f"🌐 Downloading from URL: {input_data}")
        os.makedirs(temp_dir, exist_ok=True)
        file_path = os.path.abspath(os.path.join(temp_dir, output_filename))
        return download_file_from_url(input_data, file_path)

    elif input_type == "base64":
        logger.info(f"🔢 Decoding Base64 image...")
        return save_base64_to_file(input_data, temp_dir, output_filename)

    else:
        raise Exception(f"Unsupported input type: {input_type}")


def download_file_from_url(url, output_path):
    try:
        result = subprocess.run([
            'wget', '-O', output_path, '--no-verbose', url
        ], capture_output=True, text=True)

        if result.returncode == 0:
            logger.info(f"✅ URL download success: {url}")
            return output_path
        else:
            logger.error(f"❌ wget failed: {result.stderr}")
            raise Exception(f"URL download failed: {result.stderr}")

    except Exception as e:
        logger.error(f"❌ Error downloading: {e}")
        raise Exception(f"Download error: {e}")


def save_base64_to_file(base64_data, temp_dir, output_filename):
    try:
        decoded_data = base64.b64decode(base64_data)
        os.makedirs(temp_dir, exist_ok=True)

        file_path = os.path.abspath(os.path.join(temp_dir, output_filename))
        with open(file_path, 'wb') as f:
            f.write(decoded_data)

        logger.info(f"✅ Saved Base64 image: {file_path}")
        return file_path

    except (binascii.Error, ValueError) as e:
        logger.error(f"❌ Base64 decode failed: {e}")
        raise Exception(f"Base64 decode failed: {e}")


def queue_prompt(prompt):
    url = f"http://{server_address}:8188/prompt"
    logger.info(f"Queueing prompt → {url}")
    p = {"prompt": prompt, "client_id": client_id}
    data = json.dumps(p).encode('utf-8')
    req = urllib.request.Request(url, data=data)
    return json.loads(urllib.request.urlopen(req).read())


def get_history(prompt_id):
    url = f"http://{server_address}:8188/history/{prompt_id}"
    with urllib.request.urlopen(url) as response:
        return json.loads(response.read())


def get_videos(ws, prompt):
    prompt_id = queue_prompt(prompt)['prompt_id']
    output_videos = {}

    while True:
        out = ws.recv()
        if isinstance(out, str):
            data = json.loads(out)
            if data['type'] == 'executing':
                node = data['data']['node']
                if node is None and data['data']['prompt_id'] == prompt_id:
                    break

    history = get_history(prompt_id)[prompt_id]
    for node_id in history['outputs']:
        node_out = history['outputs'][node_id]
        videos_output = []

        if 'gifs' in node_out:
            for video in node_out['gifs']:
                with open(video['fullpath'], 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('utf-8')
                videos_output.append(b64)

        output_videos[node_id] = videos_output

    return output_videos


def load_workflow(path):
    with open(path, 'r') as f:
        return json.load(f)


def handler(job):
    job_input = job.get("input", {})
    logger.info(f"Received job: {job_input}")

    task_id = f"task_{uuid.uuid4()}"
    os.makedirs(task_id, exist_ok=True)

    # -----------------------------
    # IMAGE PROCESSING (OPTIONAL)
    # -----------------------------
    if "image_path" in job_input:
        image_path = process_input(job_input["image_path"], task_id, "input.jpg", "path")

    elif "image_url" in job_input:
        image_path = process_input(job_input["image_url"], task_id, "input.jpg", "url")

    elif "image_base64" in job_input:
        image_path = process_input(job_input["image_base64"], task_id, "input.jpg", "base64")

    else:
        # AUTO BLANK IMAGE (PROMPT-ONLY MODE)
        logger.info("⚠ No image given → Generating blank image")
        blank = Image.new("RGB", (512, 512), (0, 0, 0))
        blank_path = os.path.abspath(os.path.join(task_id, "blank.jpg"))
        blank.save(blank_path)
        image_path = blank_path
        logger.info(f"🟩 Auto Blank Image: {image_path}")

    # END IMAGE (OPTIONAL)
    end_image = None
    if "end_image_url" in job_input:
        end_image = process_input(job_input["end_image_url"], task_id, "end.jpg", "url")

    # Workflow selection
    workflow_file = "/new_Wan22_flf2v_api.json" if end_image else "/new_Wan22_api.json"
    prompt = load_workflow(workflow_file)

    # Basic inputs
    prompt["244"]["inputs"]["image"] = image_path
    prompt["541"]["inputs"]["num_frames"] = job_input.get("length", 81)

    prompt["135"]["inputs"]["positive_prompt"] = job_input["prompt"]
    prompt["135"]["inputs"]["negative_prompt"] = job_input.get(
        "negative_prompt",
        "low quality, worst quality, blurry, distorted, ugly, extra fingers, bad anatomy"
    )

    prompt["220"]["inputs"]["seed"] = job_input.get("seed", 42)
    prompt["540"]["inputs"]["seed"] = job_input.get("seed", 42)
    prompt["540"]["inputs"]["cfg"] = job_input.get("cfg", 2.5)

    # Resolution
    w = job_input.get("width", 480)
    h = job_input.get("height", 832)

    aw = to_nearest_multiple_of_16(w)
    ah = to_nearest_multiple_of_16(h)

    prompt["235"]["inputs"]["value"] = aw
    prompt["236"]["inputs"]["value"] = ah

    # WebSocket connect
    ws_url = f"ws://{server_address}:8188/ws?clientId={client_id}"
    logger.info(f"Connecting WebSocket → {ws_url}")

    ws = websocket.WebSocket()
    for _ in range(60):
        try:
            ws.connect(ws_url)
            break
        except:
            time.sleep(1)

    # Run
    videos = get_videos(ws, prompt)
    ws.close()

    for node in videos:
        if videos[node]:
            return {"video": videos[node][0]}

    return {"error": "No video output generated."}


runpod.serverless.start({"handler": handler})
