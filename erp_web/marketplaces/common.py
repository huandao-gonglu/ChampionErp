from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

APP_DIR = Path(__file__).resolve().parents[2]
MARKETPLACE_CACHE_DIR = APP_DIR / "data" / "cache" / "marketplaces"
ML_CATEGORY_CACHE_PATH = MARKETPLACE_CACHE_DIR / "ml_category_cache.json"
ML_CATEGORY_TREE_PATH = MARKETPLACE_CACHE_DIR / "ml_category_tree.json"
ML_CATEGORY_SHIPPING_CACHE_PATH = MARKETPLACE_CACHE_DIR / "ml_category_shipping_cache.json"

ML_CATEGORY_WORDS = {
    "Home, Furniture and Garden": "家居、家具和花园",
    "Garden Tools": "花园工具",
    "Garden Multi Tools": "花园多功能工具",
    "Camping Equipment": "露营装备",
    "Sports and Fitness": "运动与健身",
    "Kitchen & Housewares": "厨房和家居用品",
    "Health and Health Supplies": "健康与医疗用品",
    "Industrial and Offices": "工业与办公",
    "Home": "家居",
    "Furniture": "家具",
    "Garden": "花园",
    "Kitchen": "厨房",
    "Housewares": "家居用品",
    "Tools": "工具",
    "Tool": "工具",
    "Storage": "收纳",
    "Organization": "整理收纳",
    "Sports": "运动",
    "Fitness": "健身",
    "Camping": "露营",
    "Hunting": "狩猎",
    "Fishing": "钓鱼",
    "Equipment": "装备",
    "Accessories": "配件",
    "Cycling": "骑行",
    "Medical": "医疗",
    "Dental": "牙科",
    "Industrial": "工业",
    "Office": "办公",
    "School": "学校",
    "Supplies": "用品",
    "Games": "游戏",
    "Toys": "玩具",
    "Workshop": "工作间",
    "Baking": "烘焙",
    "Utensils": "器具",
    "Tables": "桌子",
    "Chairs": "椅子",
    "Stools": "凳子",
    "Multi-function": "多功能",
    "Multi": "多",
    "function": "功能",
    "Oscillating": "摆动",
    "Carving": "雕刻",
}

ML_CATEGORY_CN_HINTS = {
    "Home": "家居",
    "Furniture": "家具",
    "Garden": "园艺",
    "Kitchen": "厨房",
    "Housewares": "家居用品",
    "Storage": "收纳",
    "Organization": "整理收纳",
    "Beauty": "美妆",
    "Health": "健康",
    "Sports": "运动",
    "Outdoors": "户外",
    "Tools": "工具",
    "Automotive": "汽车",
    "Toys": "玩具",
    "Baby": "母婴",
    "Clothing": "服装",
    "Shoes": "鞋",
    "Jewelry": "饰品",
    "Lighting": "照明",
    "Bath": "浴室",
    "Cleaning": "清洁",
    "Pet": "宠物",
    "Electronics": "电子",
    "Computers": "电脑",
    "Office": "办公",
}

CN_CATEGORY_TERMS = {
    "瓶": ["bottle", "bottles", "botella", "botellas", "frasco"],
    "水瓶": ["water bottle", "botella de agua", "termo"],
    "酒瓶": ["liquor flask", "botella de licor", "flask"],
    "杯": ["cup", "mug", "vaso", "taza"],
    "厨房": ["kitchen", "cocina"],
    "厨具": ["kitchenware", "utensilios de cocina"],
    "烘焙": ["baking", "reposteria"],
    "收纳": ["storage", "organization", "almacenamiento", "organizacion"],
    "家居": ["home", "hogar"],
    "家具": ["furniture", "muebles"],
    "工具": ["tools", "herramientas"],
    "玩具": ["toys", "juguetes"],
    "宠物": ["pet", "pets", "mascotas"],
    "汽车": ["car", "auto", "automotriz"],
    "手机": ["cell phone", "mobile phone", "celular"],
    "电脑": ["computer", "computadora"],
    "服装": ["clothing", "ropa"],
    "鞋": ["shoes", "zapatos"],
    "包": ["bag", "bags", "bolsa"],
    "饰品": ["jewelry", "joyeria", "accessories"],
    "灯": ["lighting", "lamp", "lampara"],
    "浴室": ["bathroom", "bano"],
    "清洁": ["cleaning", "limpieza"],
    "运动": ["sports", "deportes"],
    "户外": ["outdoor", "aire libre"],
    "母婴": ["baby", "bebes"],
    "美容": ["beauty", "belleza"],
    "化妆": ["makeup", "maquillaje"],
}

CN_WB_TERMS = {
    "瓶": ["бутылка", "бутылки", "флакон", "термос"],
    "水瓶": ["бутылка для воды", "термос"],
    "杯": ["кружка", "стакан", "чашка"],
    "厨房": ["кухня", "кухонные принадлежности"],
    "厨具": ["посуда", "кухонные принадлежности"],
    "收纳": ["хранение", "органайзер"],
    "家居": ["дом", "товары для дома"],
    "工具": ["инструменты"],
    "玩具": ["игрушки"],
    "宠物": ["товары для животных"],
    "汽车": ["авто", "автотовары"],
    "手机": ["телефон", "смартфон"],
    "服装": ["одежда"],
    "鞋": ["обувь"],
    "包": ["сумка"],
    "饰品": ["бижутерия", "аксессуары"],
    "灯": ["лампа", "освещение"],
    "浴室": ["ванная"],
    "清洁": ["уборка"],
    "运动": ["спорт"],
    "户外": ["туризм"],
    "母婴": ["детские товары"],
    "美容": ["красота"],
}

__all__ = [name for name in globals() if not name.startswith("__")]
