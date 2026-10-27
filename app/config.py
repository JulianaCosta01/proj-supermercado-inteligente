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
    def referencias(self): return self.dir_dados / "referencias"
    # Temporários e lixeira ficam DENTRO de cada área: renomear para o destino nunca cruza
    # volumes (cada área pode ser um volume separado, montado só onde é necessário).
    @property
    def tmp_quarentena(self): return self.quarentena / ".tmp"
    @property
    def tmp_imagens(self): return self.imagens / ".tmp"
    @property
    def tmp_referencias(self): return self.referencias / ".tmp"

    @staticmethod
    def lixeira_de(area): return area / ".lixeira"

    def preparar_diretorios(self):
        """Cria o que for possível; num contêiner, áreas montadas só para leitura são puladas."""
        for area in (self.quarentena, self.imagens, self.referencias):
            for d in (area, area / ".tmp", area / ".lixeira"):
                try:
                    d.mkdir(parents=True, exist_ok=True)
                    os.chmod(d, 0o700)
                except OSError:
                    pass
