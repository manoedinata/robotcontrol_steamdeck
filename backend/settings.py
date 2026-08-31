class Settings:
    def __init__(
        self,
        udp_ip: str = "127.0.0.1",
        udp_port: int = 8888,
        udp_listen_port: int = 8889,
        camera_url: str = "rtsp://admin:password@127.0.0.1:554/stream",
        camera_backend: str = "go2rtc",
        ptz_ip: str = "",
        ptz_username: str = "",
        ptz_password: str = "",
    ):
        self.udp_ip = udp_ip
        self.udp_port = udp_port
        self.udp_listen_port = udp_listen_port
        self.camera_url = camera_url
        self.camera_backend = camera_backend
        self.ptz_ip = ptz_ip
        self.ptz_username = ptz_username
        self.ptz_password = ptz_password
