import os
import json
from server.config import log_msg

def inspect_template_schema(template_path):
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            wf = json.load(f)
    except Exception:
        return None

    load_image_nodes = {}
    h3_easy_node = None
    for nid, node in wf.items():
        ctype = node.get("class_type", "")
        if ctype == "LoadImage":
            load_image_nodes[nid] = node
        elif ctype == "MiniMaxH3Easy":
            h3_easy_node = node

    has_scene = False
    default_scene = ""
    default_model = ""
    default_apparel = ""
    prompt_summary = ""

    if h3_easy_node:
        inputs = h3_easy_node.get("inputs", {})
        prompt = inputs.get("prompt", "")
        prompt_summary = prompt[:80].replace("\r", " ").replace("\n", " ") + "..."

        if "media_3" in inputs and inputs["media_3"]:
            has_scene = True
            scene_nid = str(inputs["media_3"][0])
            if scene_nid in load_image_nodes:
                default_scene = os.path.basename(load_image_nodes[scene_nid].get("inputs", {}).get("image", ""))

        if "media_1" in inputs and inputs["media_1"]:
            m_nid = str(inputs["media_1"][0])
            if m_nid in load_image_nodes:
                default_model = os.path.basename(load_image_nodes[m_nid].get("inputs", {}).get("image", ""))

        if "media_2" in inputs and inputs["media_2"]:
            a_nid = str(inputs["media_2"][0])
            if a_nid in load_image_nodes:
                default_apparel = os.path.basename(load_image_nodes[a_nid].get("inputs", {}).get("image", ""))

    filename = os.path.basename(template_path)
    clean_name = os.path.splitext(filename)[0]

    return {
        "filename": filename,
        "name": clean_name,
        "has_scene": has_scene,
        "default_scene": default_scene,
        "default_model": default_model,
        "default_apparel": default_apparel,
        "prompt_summary": prompt_summary
    }

