"""The Premium injury bulletin and suspensions board, as copied each week.

liga.record.pt/conteudos/boletim-clinico.aspx lists every injured player in the
league, and quadro-negro.aspx every suspended player and coach. Both are Premium
pages that open only in a browser where the manager is signed in, and this
project deliberately holds no login to the site. So they are read there before
each lock and copied into `data/boletim/<date>.json`, which is gitignored: it is
paid content, not ours to republish.

Until 18/09/2026 only the squad's own absences reached the model, typed by hand
into data/indisponiveis.yaml. The transfer search saw nothing else, and that day
it proposed Santi García, who was on the bulletin.

A SNAPSHOT NAMES ITS ROUND and applies to that round only, like the hand file:
a man injured last week may be fit this one, and a stale list would leave him out
without a word. The bulletin itself can sit unchanged for days, so a snapshot is
evidence about the day it was read, not a promise about the match.

THE REASONS IT GIVES DO NOT NAME THE SOURCE. They reach the public pages and the
tracked ledger beside the manager's own players, and "injured on 18/09" is team
news where "per the paid bulletin of 18/09" would be citing, and in time
republishing, a subscription. Found by the code review of 18/09/2026.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .base import SquadSourceError
from .manual import load_back, load_unavailable

#: The two player lists a snapshot carries, and the word each gives as a reason.
LISTS = (("lesionados", "lesionado"), ("castigados", "castigado"))

#: Snapshots are named by the day they were read, which is also what orders them.
NAMED = re.compile(r"\d{4}-\d{2}-\d{2}")


def _fold(text: str) -> str:
    """Lower case, no accents, single spaces — the site is not consistent."""
    stripped = "".join(
        c
        for c in unicodedata.normalize("NFD", (text or "").lower())
        if unicodedata.category(c) != "Mn"
    )
    return " ".join(stripped.split())


def load_bulletin(directory: str | Path, round_number: int) -> dict[str, Any] | None:
    """The newest snapshot read for `round_number`, or None if there is none.

    Files are named by the date they were read, YYYY-MM-DD.json, so the last one
    in name order is the newest. A file named any other way would break that
    order without a word, so it is refused. A snapshot naming another round is
    left alone, not applied.
    """
    folder = Path(directory)
    if not folder.is_dir():
        return None
    newest = None
    for file in sorted(folder.glob("*.json")):
        if not NAMED.fullmatch(file.stem):
            raise SquadSourceError(
                f"{file.name}: snapshots are named by the day they were read, "
                "as YYYY-MM-DD.json — that name is what orders them"
            )
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise SquadSourceError(f"{file} is not valid JSON: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("lesionados"), list):
            raise SquadSourceError(f"{file} is not a bulletin snapshot")
        if int(doc.get("jornada", -1)) == int(round_number):
            newest = doc
    return newest


def read_day(snapshot: Mapping[str, Any] | None) -> str | None:
    """The day a snapshot was read, as dd/mm, or None."""
    read = str((snapshot or {}).get("lido_em") or "")
    return f"{read[8:10]}/{read[5:7]}" if len(read) >= 10 else None


def _entries(snapshot: Mapping[str, Any]):
    for key, why in LISTS:
        for entry in snapshot.get(key) or ():
            yield entry, why


def bulletin_out(
    snapshot: Mapping[str, Any] | None, players: Iterable[Any]
) -> dict[str, str]:
    """Every player on the snapshot, by id, with the reason — on name AND club.

    Never on the name alone. The bulletin of 18/09 listed a Samu of FC Porto,
    and the squad holds the Samu of V. Guimarães.
    """
    if not snapshot:
        return {}
    day = read_day(snapshot)
    wanted: dict[tuple[str, str], str] = {}
    for entry, why in _entries(snapshot):
        key = (_fold(entry.get("nome", "")), _fold(entry.get("clube", "")))
        wanted[key] = f"{why} a {day}" if day else why
    out: dict[str, str] = {}
    for player in players:
        reason = wanted.get((_fold(player.name), _fold(player.club)))
        if reason is not None:
            out[player.id] = reason
    return out


def unmatched(snapshot: Mapping[str, Any] | None, players: Iterable[Any]) -> list[str]:
    """Names on the snapshot that match no player: naming drift, made visible.

    A name or a club spelled differently from the market's matches nobody, and
    the man it means is then never left out. That failure is silent unless it is
    counted. On 18/09/2026 all forty-three matched.
    """
    if not snapshot:
        return []
    have = {(_fold(p.name), _fold(p.club)) for p in players}
    return [
        f"{entry.get('nome')} ({entry.get('clube')})"
        for entry, _ in _entries(snapshot)
        if (_fold(entry.get("nome", "")), _fold(entry.get("clube", ""))) not in have
    ]


def known_out(
    unavailable_path: str | Path,
    bulletin_dir: str | Path,
    round_number: int,
    players: Iterable[Any],
) -> dict[str, str]:
    """Who among `players` cannot play this round, and why.

    The bulletin first, then the hand file on top of it. The hand file wins
    because it is where the lock-day ALERTA goes, and that is fresher than a
    bulletin that can sit unchanged for days. Its `aptos` list is how a man the
    bulletin still names is put back.
    """
    out = bulletin_out(load_bulletin(bulletin_dir, round_number), list(players))
    for player_id in load_back(unavailable_path, round_number):
        out.pop(player_id, None)
    out.update(load_unavailable(unavailable_path, round_number))
    return out


def stale_bulletin(directory: str | Path, round_number: int) -> str | None:
    """A reminder when this round's bulletin has not been copied yet, else None."""
    if load_bulletin(directory, round_number) is not None:
        return None
    return (
        f"  o boletim clinico da jornada {round_number} ainda nao foi copiado para "
        "data/boletim/ — le-o no Chrome antes do fecho, com o quadro negro, para o "
        "onze e a transferencia saberem quem esta fora."
    )
