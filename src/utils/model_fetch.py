"""Multi-source model downloads verified against pinned sizes and SHA-256 digests.

Every file is listed with its exact size and digest, so any mirror is acceptable: a copy
that does not match is discarded and the next source is tried. Sources are ordered by
the ``model_mirror`` setting (ModelScope / domestic proxies first, or the originals).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from src.utils import download_progress
from src.utils.file_integrity import matches_sha256, remember_sha256, sha256_file
from src.utils.http_download import DownloadError, fetch_resumable
from src.utils.logger import logger

MirrorPreference = Literal["china", "global"]
# "custom" (HF_ENDPOINT) always goes first; "fallback" hosts always go last.
Region = Literal["custom", "china", "global", "fallback"]

MODELSCOPE = "https://modelscope.cn"
HF_MIRROR = "https://hf-mirror.com"
HUGGINGFACE = "https://huggingface.co"
GITHUB = "https://github.com"
GH_PROXY = "https://gh-proxy.com/"
# Has cut transfers off mid-file and blacklists some release assets.
GH_FAST = "https://ghfast.top/"

_preference: MirrorPreference = "china"


def set_mirror_preference(value: str) -> None:
    global _preference
    _preference = "global" if value == "global" else "china"


def mirror_preference() -> MirrorPreference:
    return _preference


@dataclass(frozen=True)
class Candidate:
    url: str
    region: Region


@dataclass(frozen=True)
class ModelFile:
    path: str  # relative to the model directory (and to the repo root)
    size: int
    sha256: str


def modelscope_url(repo: str, path: str) -> Candidate:
    return Candidate(f"{MODELSCOPE}/models/{repo}/resolve/master/{path}", "china")


def huggingface_urls(repo: str, path: str, revision: str = "main") -> list[Candidate]:
    suffix = f"/{repo}/resolve/{revision or 'main'}/{path}"
    out: list[Candidate] = []
    custom = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if custom and custom not in (HF_MIRROR, HUGGINGFACE):
        out.append(Candidate(custom + suffix, "custom"))
    out.append(Candidate(HF_MIRROR + suffix, "china"))
    out.append(Candidate(HUGGINGFACE + suffix, "global"))
    return out


def github_urls(url: str) -> list[Candidate]:
    """A GitHub release asset, directly and through the domestic download proxies."""
    if not url.startswith(GITHUB):
        return [Candidate(url, "global")]
    return [
        Candidate(GH_PROXY + url, "china"),
        Candidate(url, "global"),
        Candidate(GH_FAST + url, "fallback"),
    ]


def ordered_urls(candidates: Sequence[Candidate]) -> list[str]:
    """Preferred region first; the original order is kept within a region."""
    other: Region = "global" if _preference == "china" else "china"
    rank: dict[Region, int] = {"custom": 0, _preference: 1, other: 2, "fallback": 3}
    out: list[str] = []
    for cand in sorted(candidates, key=lambda c: rank[c.region]):
        if cand.url not in out:
            out.append(cand.url)
    return out


def host_of(url: str) -> str:
    rest = url.split("://", 1)[-1]
    return rest.split("/", 1)[0]


def file_ready(directory: Path, item: ModelFile) -> bool:
    path = directory / item.path
    try:
        if not path.is_file() or path.stat().st_size != item.size:
            return False
    except OSError:
        return False
    return matches_sha256(path, item.sha256)


def files_ready(directory: Path, files: Sequence[ModelFile]) -> bool:
    return all(file_ready(directory, item) for item in files)


def fetch_files(
    files: Sequence[ModelFile],
    sources: Callable[[ModelFile], Sequence[Candidate]],
    directory: Path,
    *,
    label: str,
    key: str,
) -> None:
    """Download every missing or damaged file; raises :class:`DownloadError` on failure."""
    from src.utils.proxy_env import prepare_model_download_env, use_proxy_for_downloads

    pending = [item for item in files if not file_ready(directory, item)]
    if not pending:
        return
    prepare_model_download_env()
    trust_env = use_proxy_for_downloads()
    total = sum(item.size for item in files)
    done = total - sum(item.size for item in pending)
    download_progress.begin(key, label, dest=directory, total=total)
    download_progress.update(key, done, total)
    try:
        for item in pending:
            _fetch_one(
                item,
                ordered_urls(sources(item)),
                directory,
                key=key,
                offset=done,
                total=total,
                trust_env=trust_env,
            )
            done += item.size
            download_progress.update(key, done, total)
    except Exception as exc:
        download_progress.finish(key, ok=False, error=str(exc))
        raise
    download_progress.finish(key, ok=True)
    logger.info(f"{label} 已保存: {directory}")


def _fetch_one(
    item: ModelFile,
    urls: Sequence[str],
    directory: Path,
    *,
    key: str,
    offset: int,
    total: int,
    trust_env: bool,
) -> None:
    target = directory / item.path
    target.parent.mkdir(parents=True, exist_ok=True)
    # Every source serves identical bytes, so a partial file carries over between them.
    tmp = target.with_name(target.name + ".part")
    last_exc: Exception | None = None
    for url in urls:
        host = host_of(url)
        download_progress.set_source(key, url)
        logger.info(f"正在下载 {item.path}（{host}）: {url}")
        try:
            fetch_resumable(
                url,
                tmp,
                progress_key=key,
                progress_offset=offset,
                progress_total=total,
                trust_env=trust_env,
            )
        except Exception as exc:
            last_exc = exc
            logger.warning(f"{item.path} 从 {host} 下载失败: {exc}")
            continue
        size = tmp.stat().st_size if tmp.exists() else 0
        digest = sha256_file(tmp) if size == item.size else ""
        if digest.casefold() != item.sha256.casefold():
            tmp.unlink(missing_ok=True)
            last_exc = DownloadError(f"{host} 提供的文件校验不通过（{size} 字节）")
            logger.warning(f"{item.path} 从 {host} 下载的内容校验不通过，换下一个下载源")
            continue
        tmp.replace(target)
        remember_sha256(target, digest)
        return
    raise DownloadError(f"{item.path} 所有下载源均失败：{last_exc}")


@dataclass(frozen=True)
class RepoManifest:
    """A model repository: its files, Hugging Face origin and ModelScope copies."""

    huggingface: str
    revision: str
    files: tuple[ModelFile, ...]
    modelscope: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return sum(item.size for item in self.files)

    def candidates(self, item: ModelFile) -> list[Candidate]:
        return [
            *(modelscope_url(repo, item.path) for repo in self.modelscope),
            *huggingface_urls(self.huggingface, item.path, self.revision),
        ]

    def ready(self, directory: Path) -> bool:
        return files_ready(directory, self.files)

    def fetch(self, directory: Path, *, label: str, key: str) -> None:
        fetch_files(self.files, self.candidates, directory, label=label, key=key)

    def source_hosts(self) -> tuple[str, ...]:
        hosts = ["modelscope.cn"] if self.modelscope else []
        hosts.append(host_of(HF_MIRROR))
        return tuple(hosts)
