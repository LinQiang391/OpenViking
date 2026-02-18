"""Configuration API"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Dict

from vikingbot.config.loader import load_config, save_config, get_config_path

router = APIRouter()


class ConfigUpdate(BaseModel):
    config: Dict[str, Any]


def get_config_schema():
    from vikingbot.config.schema import Config
    schema = Config.model_json_schema()
    
    def process_schema(obj):
        if isinstance(obj, dict):
            result = {}
            for k, v in obj.items():
                if k in ["title", "description", "type", "properties", "required", "default", "anyOf", "allOf", "oneOf", "items"]:
                    result[k] = process_schema(v)
            return result
        elif isinstance(obj, list):
            return [process_schema(item) for item in obj]
        return obj
    
    return process_schema(schema)


@router.get("/config")
async def get_config():
    try:
        config = load_config()
        config_dict = config.model_dump()
        return {
            "success": True,
            "data": config_dict
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/config/schema")
async def get_config_schema_endpoint():
    try:
        schema = get_config_schema()
        return {
            "success": True,
            "data": schema
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/config")
async def update_config(update: ConfigUpdate):
    try:
        from vikingbot.config.schema import Config
        config = Config(**update.config)
        save_config(config)
        return {
            "success": True,
            "message": "Config updated"
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/config/path")
async def get_config_path_endpoint():
    return {
        "success": True,
        "data": {
            "path": str(get_config_path())
        }
    }
