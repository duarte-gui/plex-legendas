"""Configuração lida do ambiente. Nada de credencial no código."""
import os


class ConfigError(RuntimeError):
    pass


def _req(nome: str) -> str:
    v = os.environ.get(nome, "").strip()
    if not v:
        raise ConfigError(
            f"Variável {nome} não definida. "
            "Copie config.example.env para .env e preencha antes de rodar."
        )
    return v


class Config:
    """Endereços e credenciais dos serviços."""

    def __init__(self) -> None:
        self.plex_url = _req("PLEX_URL").rstrip("/")
        self.plex_token = _req("PLEX_TOKEN")

        # Bazarr é opcional: só necessário para exportar as legendas como arquivo.
        self.bazarr_url = os.environ.get("BAZARR_URL", "").rstrip("/")
        self.bazarr_key = os.environ.get("BAZARR_APIKEY", "")

        # Score mínimo aceito. Abaixo disso o candidato é descartado.
        # Acertos reais costumam passar de 1000; correspondências espúrias
        # ficam abaixo de algumas centenas.
        self.score_min = int(os.environ.get("SCORE_MIN", "1000"))

        # Idioma no formato que o Plex espera (ex.: pt-BR, es-ES, fr-FR).
        self.idioma = os.environ.get("IDIOMA", "pt-BR")

        self.porta = int(os.environ.get("PORTA", "8770"))

    @property
    def tem_bazarr(self) -> bool:
        return bool(self.bazarr_url and self.bazarr_key)


def carregar_dotenv(caminho: str = ".env") -> None:
    """Carrega um .env simples, se existir. Não sobrescreve o ambiente."""
    if not os.path.exists(caminho):
        return
    with open(caminho, encoding="utf-8") as f:
        for linha in f:
            linha = linha.strip()
            if not linha or linha.startswith("#") or "=" not in linha:
                continue
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip().strip("'\""))
