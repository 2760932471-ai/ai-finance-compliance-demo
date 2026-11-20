"""Local evidence checks; no model/network calls and no authenticity verdicts."""
import re
from collections import Counter
from datetime import date
from pathlib import Path
from PIL import Image
from . import store

VERSION = 'local-evidence-checks-v1'


def image_metadata(path):
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            details = exif.get_ifd(34665) if 34665 in exif else {}
            gps = exif.get_ifd(34853) if 34853 in exif else {}
            def text(value):
                if isinstance(value, bytes): value = value.decode('utf-8', errors='replace')
                return str(value or '').strip('\x00 ')[:200]
            shot = text(details.get(36867) or exif.get(36867))
            device = text(exif.get(272))
            coordinates = None
            try:
                def degrees(values):
                    d, m, s = map(float, values)
                    return d + m / 60 + s / 3600
                lat, lon = degrees(gps[2]), degrees(gps[4])
                lat_ref, lon_ref = text(gps[1]), text(gps[3])
                if lat_ref not in ('N', 'S') or lon_ref not in ('E', 'W'): raise ValueError()
                if lat_ref == 'S': lat = -lat
                if lon_ref == 'W': lon = -lon
                if not (-90 <= lat <= 90 and -180 <= lon <= 180): raise ValueError()
                coordinates = {'latitude': round(lat, 7), 'longitude': round(lon, 7)}
            except (KeyError, TypeError, ValueError, ZeroDivisionError): pass
            missing = [label for label, value in [('拍摄时间', shot), ('设备型号', device), ('GPS坐标', coordinates)] if not value]
            return {'status': 'read', 'exif_present': bool(exif), 'image_size': list(image.size),
                    'captured_at': shot, 'device': device, 'gps': coordinates, 'missing': missing,
                    'note': '读取原文件元数据，不证明其真实性；缺失也不等于伪造。'}
    except Exception:
        return {'status': 'error', 'note': '无法读取原图元数据，不能视为EXIF缺失。'}


def collect(evidence, documents):
    calendar = []
    for e in evidence:
        if e.get('category') != '现场照片': continue
        text = re.sub(r'(?<=[\u3400-\u9fff\d]) +(?=[\u3400-\u9fff\d])', '', e.get('text', ''))
        matches = list(re.finditer(r'(20\d{2})[年/.-](\d{1,2})[月/.-](\d{1,2})日?\s*(?:星期|周)([一二三四五六日天])', text))
        if not matches:
            calendar.append({'evidence_id': e['id'], 'status': 'unavailable', 'note': '未提取到完整日期和星期，需对照原图。'})
        for match in matches:
            item = {'evidence_id': e['id'], 'observed': match.group(0), 'source': e.get('method', ''), 'status': 'invalid'}
            try:
                day = date(*map(int, match.groups()[:3]))
                expected = '一二三四五六日'[day.weekday()]
                item.update(date=day.isoformat(), expected='星期' + expected,
                            status='matched' if match.group(4).replace('天', '日') == expected else 'conflict')
            except ValueError: item['note'] = '日期文本无法构成有效公历日期，需核对识别文字。'
            calendar.append(item)
    photos = []
    for d in documents:
        if d.get('category') != '现场照片' or Path(d.get('name', '')).suffix.lower() not in ('.jpg', '.jpeg', '.png'): continue
        metadata = image_metadata(d['path']) if d.get('path') else d.get('image_metadata', {'status': 'unavailable', 'note': '原件路径不可用'})
        photos.append({'document_id': d['id'], 'source_sha256': d.get('sha256', ''), **metadata})
    sizes = Counter(tuple(x['image_size']) for x in photos if x.get('image_size'))
    return {'version': VERSION, 'checked_at': store.now(), 'calendar': calendar, 'photo_metadata': photos,
            'repeated_sizes': [{'size': list(k), 'count': v} for k, v in sizes.items() if v > 1],
            'note': '仅核对识别文字的公历一致性与原图元数据；尺寸相同、缺EXIF或水印匹配均不能单独认定真伪。'}


def snapshot(run):
    if run.get('technical_checks'): return run['technical_checks']
    return {**collect(run.get('evidence', []), run.get('documents', [])),
            'supplemental': True, 'scope_note': '当前生成的本地补充核验，未参与本次历史模型判断，不回写原判。'}
