"""Configuração por variáveis de ambiente. Nenhum segredo tem valor padrão."""
import os
import pathlib
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Config:
    dsn_app: str = field(default_factory=lambda: os.environ.get("STP_DSN_APP", ""))
    dsn_worker: str = field(default_factory=lambda: os.environ.get("STP_DSN_WORKER", ""))
    dir_dados: pathlib.Path = field(default_factory=lambda: pathlib.Path(os.environ.get("STP_DIR_DADOS", "/var/lib/stp")))
    origem_permitida: str = field(default_factory=lambda: os.environ.get("STP_ORIGEM", "https://localhost"))
    cookie_seguro: bool = field(default_factory=lambda: os.environ.get("STP_COOKIE_SEGURO", "1") == "1")
    max_upload_bytes: int = 15 * 1024 * 1024
    max_csv_bytes: int = 5 * 1024 * 1024
    max_csv_linhas: int = 50_000
    reserva_disco_bytes: int = field(default_factory=lambda: int(os.environ.get("STP_RESERVA_DISCO", str(500 * 1024 * 1024))))
    sessao_horas: int = 8

    @property
    def quarentena(self): return self.dir_dados / "quarentena"
    @property
    def imagens(self): return self.dir_dados / "imagens"
    @property
    def tmp(self): return self.dir_dados / "tmp"
    @property
    def lixeira(self): return self.dir_dados / "lixeira"

    def preparar_diretorios(self):
        for d in (self.quarentena, self.imagens, self.tmp, self.lixeira):
            d.mkdir(parents=True, exist_ok=True)
            os.chmod(d, 0o700)
