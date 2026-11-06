from lxml import etree


def parse(data):
    return etree.fromstring(data)
