"""Bold word-active captions, rasterized only when the highlighted word changes."""
from bisect import bisect_right
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import moderngl

from .effects.base import QUAD_VERTEX_SHADER
from .karaoke import validate_timeline


class CaptionPainter:
    def __init__(self, timeline, width, height, font_path=None):
        self.words = validate_timeline(timeline)['words']
        self.width, self.height = width, height
        size = round(min(width / 23, height / 13))
        self.font = ImageFont.truetype(font_path or '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', size)
        self.small = ImageFont.truetype(font_path or '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf', max(12, size // 3))
        self.size = size
        self.phrases = []
        phrase = []
        measure = ImageDraw.Draw(Image.new('RGBA', (1, 1)))
        for word in self.words:
            text = ' '.join(w['word'] for w in phrase + [word])
            too_wide = measure.textlength(text, font=self.font) > width * 0.84
            split = phrase and (word['start'] - phrase[-1]['end'] > 0.8 or
                                word.get('segment') != phrase[-1].get('segment') or
                                len(phrase) >= 8 or too_wide)
            if split:
                self.phrases.append(phrase)
                phrase = []
            phrase.append(word)
        if phrase:
            self.phrases.append(phrase)
        self.starts = [p[0]['start'] for p in self.phrases]

    def state(self, time):
        index = bisect_right(self.starts, time) - 1
        if index < 0:
            return (-1, -1)
        phrase = self.phrases[index]
        # A short release keeps phrases readable; silence never keeps a word lit.
        next_start = self.starts[index+1] if index+1 < len(self.starts) else float('inf')
        if time >= min(phrase[-1]['end'] + 0.25, next_start):
            return (-1, -1)
        active = next((i for i, word in enumerate(phrase) if word['start'] <= time < word['end']), -1)
        return index, active

    def paint(self, state):
        image = Image.new('RGBA', (self.width, self.height))
        index, active = state
        if index < 0:
            return image
        phrase = self.phrases[index]
        draw = ImageDraw.Draw(image)
        widths = [draw.textlength(w['word'], font=self.font) for w in phrase]
        gap = draw.textlength(' ', font=self.font)
        total = sum(widths) + gap * (len(phrase)-1)
        # Very long single words scale down rather than clipping.
        font = self.font
        if total > self.width * .86:
            font = ImageFont.truetype(self.font.path, max(10, int(self.size * self.width * .86 / total)))
            widths = [draw.textlength(w['word'], font=font) for w in phrase]
            gap = draw.textlength(' ', font=font)
            total = sum(widths) + gap * (len(phrase)-1)
        x = (self.width - total) / 2
        y = self.height * (0.79 if self.height > self.width else 0.84)
        pad = self.size * .5
        draw.rounded_rectangle((x-pad, y-pad*.7, x+total+pad, y+self.size+pad*.8),
                               radius=self.size*.35, fill=(8, 12, 26, 220), outline=(90, 125, 180, 130), width=2)
        draw.rounded_rectangle((x, y-pad*.7, x+min(total, self.size*1.2), y-pad*.7+3), radius=1, fill=(60, 224, 255, 255))
        for i, (word, length) in enumerate(zip(phrase, widths)):
            color = (255, 221, 75, 255) if i == active else (246, 248, 255, 255)
            if i == active:
                draw.rounded_rectangle((x-5, y-3, x+length+5, y+self.size+5), radius=7, fill=(85, 64, 18, 210))
            draw.text((x, y), word['word'], font=font, fill=color, stroke_width=2,
                      stroke_fill=(0, 0, 0, 255), anchor='lt')
            x += length + gap
        return image


class CaptionOverlay:
    def __init__(self, ctx, width, height, path, font_path=None):
        import numpy as np
        self.painter = CaptionPainter(json.loads(Path(path).read_text()), width, height, font_path)
        self.ctx = ctx
        self.texture = ctx.texture((width, height), 4)
        self.texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.prog = ctx.program(vertex_shader=QUAD_VERTEX_SHADER, fragment_shader='''
            #version 330
            uniform sampler2D caption;
            in vec2 v_uv;
            out vec4 color;
            void main() { color = texture(caption, v_uv); }
        ''')
        self.vbo = ctx.buffer(np.array([-1,-1, 1,-1, -1,1, 1,1], dtype='f4').tobytes())
        self.vao = ctx.simple_vertex_array(self.prog, self.vbo, 'in_vert')
        self.previous = None

    def render(self, time):
        state = self.painter.state(time)
        if state[0] < 0:
            return
        if state != self.previous:
            self.texture.write(self.painter.paint(state).tobytes())
            self.previous = state
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = (moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA)
        self.texture.use(0)
        self.prog['caption'].value = 0
        self.vao.render(moderngl.TRIANGLE_STRIP)

    def destroy(self):
        for resource in (self.vao, self.vbo, self.prog, self.texture):
            resource.release()
