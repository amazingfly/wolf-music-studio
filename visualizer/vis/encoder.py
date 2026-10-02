import os
import subprocess
import tempfile


class FFmpegEncoder:
    """Manages an active FFmpeg video encoding subprocess with audio muxing."""

    def __init__(
        self,
        audio_path: str,
        output_mp4: str,
        width: int,
        height: int,
        fps: int = 30,
        use_vaapi: bool = True,
        vaapi_device: str = "/dev/dri/renderD128",
        video_bitrate: str = None,
        audio_bitrate: str = "192k",
    ):
        self.audio_path = audio_path
        self.output_mp4 = output_mp4
        self.width = width
        self.height = height
        self.fps = fps
        self.use_vaapi = use_vaapi and os.path.exists(vaapi_device)
        self.vaapi_device = vaapi_device
        self.video_bitrate = video_bitrate
        self.audio_bitrate = audio_bitrate
        self.proc = None
        self.log = None

    def start(self):
        """Starts the FFmpeg subprocess piping rawvideo from stdin."""
        os.makedirs(os.path.dirname(os.path.abspath(self.output_mp4)), exist_ok=True)

        cmd = [
            'ffmpeg', '-y',
            '-f', 'rawvideo',
            '-vcodec', 'rawvideo',
            '-s', f'{self.width}x{self.height}',
            '-pix_fmt', 'rgb24',
            '-r', str(self.fps),
            '-i', '-',
            '-i', self.audio_path,
        ]

        if self.use_vaapi:
            cmd.extend([
                '-vaapi_device', self.vaapi_device,
                '-vf', 'format=nv12,hwupload',
                '-c:v', 'h264_vaapi',
            ])
            if self.video_bitrate:
                cmd.extend(['-b:v', self.video_bitrate])
        else:
            cmd.extend([
                '-c:v', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-pix_fmt', 'yuv420p',
            ])
            if self.video_bitrate:
                cmd.extend(['-b:v', self.video_bitrate])

        cmd.extend([
            '-c:a', 'aac',
            '-b:a', self.audio_bitrate,
            '-shortest',
            self.output_mp4,
        ])

        self.log = tempfile.TemporaryFile(mode='w+b')
        print(f"[+] Video encoder: {'h264_vaapi (' + self.vaapi_device + ')' if self.use_vaapi else 'libx264'}")
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=self.log)
        return self

    def write_frame(self, frame_bytes: bytes):
        """Writes raw frame bytes directly to FFmpeg stdin."""
        if self.proc and self.proc.stdin:
            self.proc.stdin.write(frame_bytes)

    def close(self):
        """Closes FFmpeg stdin and waits for process to finish."""
        if self.proc:
            if self.proc.stdin:
                try:
                    self.proc.stdin.close()
                except BrokenPipeError:
                    pass
            code = self.proc.wait()
            self.proc = None
            self.log.seek(0)
            diagnostic = self.log.read().decode(errors='replace')[-4000:]
            self.log.close()
            self.log = None
            if code:
                raise RuntimeError(f'FFmpeg failed ({code}):\n{diagnostic}')
