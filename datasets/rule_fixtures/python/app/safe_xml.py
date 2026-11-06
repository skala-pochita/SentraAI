# 음성: defusedxml
from defusedxml import ElementTree as ET
from lxml import etree


def parse(data):
    parser = etree.XMLParser(resolve_entities=False)
    return ET.fromstring(data)
