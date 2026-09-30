import re
import xml.etree.ElementTree as ET
import engine
NS = '{http://openoffice.org/2001/registry}'
XML_PATH = '/org.openoffice.Office.Common/Misc'
XML_KEY = 'SymbolStyle'
COLORS = ('background', 'foreground', 'gradient')


def xml_match(text):
    ET.fromstring(text)
    matches = []
    for item in re.finditer(r'<item\b[^>]*>.*?</item>', text, re.S):
        head = item.group().split('>', 1)[0]
        if not re.search(r'oor:path=[\"\']' + re.escape(XML_PATH) + r'[\"\']', head):
            continue
        for prop in re.finditer(r'<prop\b[^>]*>.*?</prop>', item.group(), re.S):
            if re.search(r'oor:name=[\"\']' + XML_KEY + r'[\"\']', prop.group().split('>', 1)[0]):
                matches.append((item, prop))
    if len(matches) > 1:
        raise RuntimeError('Duplicate LibreOffice SymbolStyle entries')
    return matches[0] if matches else None


def xml_get(text):
    match = xml_match(text)
    if not match:
        return engine.MISSING
    value = re.search(r'<value(?:\s[^>]*)?>(.*?)</value>', match[1].group(), re.S)
    if not value:
        raise RuntimeError('Unsupported SymbolStyle XML value representation')
    return value.group(1)


def xml_set(text, value):
    match = xml_match(text)
    if match:
        item, prop = match
        start, end = item.start() + prop.start(), item.start() + prop.end()
        if value == engine.MISSING:
            # Our newly inserted single-property item can be removed in full.
            remainder = item.group()[:prop.start()] + item.group()[prop.end():]
            if re.fullmatch(r'<item\b[^>]*>\s*</item>', remainder, re.S):
                end = item.end() + (1 if text[item.end():].startswith('\n') else 0)
                return text[:item.start()] + text[end:]
            return text[:start] + text[end:]
        replacement = re.sub(r'(<value(?:\s[^>]*)?>).*?(</value>)',
                             lambda m: m.group(1) + value + m.group(2), prop.group(), flags=re.S)
        return text[:start] + replacement + text[end:]
    if value == engine.MISSING:
        return text
    entry = '<item oor:path="' + XML_PATH + '"><prop oor:name="SymbolStyle" oor:op="fuse"><value>' + value + '</value></prop></item>\n'
    if text.count('</oor:items>') != 1:
        raise RuntimeError('Unsupported LibreOffice XML root')
    return text.replace('</oor:items>', entry + '</oor:items>')


