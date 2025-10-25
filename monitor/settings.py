import json

CONFIG_FILE = "config.json"

def load_config():
    try:
        with open(CONFIG_FILE,'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_config(config):
    with open(CONFIG_FILE,'w', encoding='utf-8') as f:
        json.dump(config,f, indent=4)
