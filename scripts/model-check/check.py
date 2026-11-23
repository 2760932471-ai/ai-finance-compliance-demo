"""Exercise the installed audit adapter with fresh synthetic text and image inputs."""
import secrets
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
import server  # Load this installation's .env exactly as for the local service.
from app import model
from PIL import Image, ImageDraw


def test_input(kind, folder):
    # Symbols with distinct glyphs, including when rendered by Pillow's default font.
    code = ''.join(secrets.choice('2345679ACDEFHKMNRTWXY') for _ in range(6))
    if kind == '文字':
        return code, '将这个测试码原样填入 code 字段：' + code, []
    image = Image.new('RGB', (144, 30), 'white')
    ImageDraw.Draw(image).text((4, 6), code, fill='black')
    path = folder / 'probe.png'
    image.resize((576, 120), Image.Resampling.NEAREST).save(path)
    return code, '读取图片中的六位测试码，原样填入 code 字段。', [str(path)]


def main():
    diagnostic = model.diagnostic(check_login=True)
    print('本机模型环境', diagnostic['status'], '｜登录', diagnostic['login'], flush=True)
    if diagnostic['offline'] or diagnostic['login'] != '已登录':
        return 1
    print('独立测试使用审核包的调用代码，只发送随机文字和一张测试图。', flush=True)
    with tempfile.TemporaryDirectory() as folder:
        model.DATA = Path(folder)
        passed = True
        for kind, limit in [('文字', 45), ('单张图片', 90)]:
            code, prompt, images = test_input(kind, model.DATA)
            print(f'正在测试{kind}（调用超时 {limit} 秒）…', flush=True)
            start = time.monotonic()
            try:
                value, _ = model.infer(prompt, model.object_schema({'code': model.TEXT}),
                                       images=images, timeout=limit, force=True)
                match = value == {'code': code}
                status = '通过，返回的测试码正确' if match else '已收到模型回答，但测试码不正确'
                passed = passed and match
            except (OSError, RuntimeError, ValueError) as error:
                passed = False
                status = str(error)
            print(f'{kind}实测｜{time.monotonic()-start:.1f} 秒｜{status}', flush=True)
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
