"""Record a transfer once, in the two places that both have to know.

THE FAILURE THIS EXISTS TO PREVENT. Manuel makes a transfer on the site, and
nothing here can see it — reading his own team sheet needs a login the project
deliberately does not hold. So two files have to be updated by hand:
data/squad.yaml, or every page goes on comparing him against a squad that is no
longer his, and data/decisions.json, or the ledger can never answer whether
following the model would have paid.

Forget either and nothing breaks. The pages render, every number beside the
wrong name is correctly computed, and no error is raised anywhere. That is the
worst shape a bug can have, and it is one command away from never happening.

    python scripts/transferir.py --sai "Tiago Esgaio" --entra Vagiannidis
    python scripts/transferir.py --sai X --entra Y --jornada 4
    python scripts/transferir.py --sai X --entra Y --ensaio      # só mostra
    python scripts/transferir.py --capitao Begraoui              # só a braçadeira

WHAT IT REFUSES, rather than doing quietly:

  * a name that matches no player, or more than one
  * selling a man who is not in the squad, or buying one who already is
  * a squad that would break §6.6's quota or §6.4's budget
  * a team sheet that would stop being legal under §6.13
  * a round already recorded in the ledger — a decision is a matter of record

The YAML is edited surgically rather than reloaded and dumped. That file is
half comments, and they are the documentation: the position groups, the
per-player projections, the reason the sheet lives there at all. A round trip
through a YAML writer would delete every one of them and leave the data intact,
which would look like it had worked.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src")]

from liga_record_mcp.models import Position  # noqa: E402
from liga_record_mcp.rules import validate_selection, validate_squad  # noqa: E402
from liga_record_mcp.source import LigaRecordClient, ManualSquadSource  # noqa: E402
from liga_record_mcp.source.decisions import (  # noqa: E402
    load_decisions,
    record_decision,
    save_decisions,
)

SQUAD_PATH = ROOT / "data" / "squad.yaml"
DECISIONS_PATH = ROOT / "data" / "decisions.json"

POS_PT = {"GK": "GR", "DEF": "DEF", "MID": "MED", "FWD": "AVA"}


class Refused(SystemExit):
    """A refusal, not a crash. Printed plainly and exits non-zero."""

    def __init__(self, why: str) -> None:
        super().__init__(f"  recusado: {why}")


def fold(text: str) -> str:
    """Accent- and case-insensitive, so "Vagiannidis" finds "Vagiannídis"."""
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)
    )
    return " ".join(stripped.lower().split())


def one_of(candidates, wanted: str, where: str):
    """Exactly one match, or a refusal naming what it saw.

    Substring matching, because he will type "Esgaio" and the site says "Tiago
    Esgaio". Ambiguity refuses rather than picking: buying the wrong player is
    not recoverable in a league that allows one transfer a round.
    """
    needle = fold(wanted)
    exact = [p for p in candidates if fold(p.name) == needle]
    if len(exact) == 1:
        return exact[0]
    hits = [p for p in candidates if needle in fold(p.name)]
    if not hits:
        raise Refused(f"não há ninguém chamado «{wanted}» {where}")
    if len(hits) > 1:
        names = ", ".join(sorted(p.name for p in hits))
        raise Refused(f"«{wanted}» {where} dá mais que um: {names}")
    return hits[0]


def player_block(player, projection: str = "") -> str:
    """One player, in the shape the file already uses."""
    note = f"          # {projection}" if projection else ""
    return (
        f'  - id: "{player.id}"{note}\n'
        f"    name: {player.name}\n"
        f"    position: {player.position.value}\n"
        f"    club: {player.club}\n"
        f"    value: {player.value}\n"
        f"    initial_value: {player.initial_value}\n"
        f"    points_total: {player.points_total}\n"
        f"    points_round: {player.points_round}\n"
    )


def find_block(text: str, player_id: str) -> tuple[int, int]:
    """Where one player's entry starts and ends in the file.

    BY INDENTATION, not by hunting for whatever comes after. The first version
    looked for the next `  - id:`, the next `  # ---` group heading, or
    `selection:`, and stopped at whichever came first. That works for every
    player but the LAST one in the file — for him only `selection:` matches,
    and the span swallowed the fourteen lines of comment above it: the
    paragraph explaining that the team sheet lives in this file at all, that
    the loader validates it before a page is drawn, and that §11.2 works down
    the bench in the order written.

    Verified against the real squad before the fix: the block came back
    twenty-two lines long, `player_block` wrote eight back, and the result
    loaded and validated cleanly — so it was saved. Losing the comments while
    the data stays correct is exactly the failure this module does text surgery
    to avoid, and it looks precisely like it worked.

    An entry is its `  - id:` line plus the lines indented under it. Anything at
    a shallower indent is not part of it — a blank line, a group heading,
    `selection:`, the end of the file — whatever it happens to be.
    """
    start = text.find(f'  - id: "{player_id}"')
    if start < 0:
        raise Refused(f"o id {player_id} não está no data/squad.yaml")
    lines = text[start:].split(chr(10))
    taken = 1
    for line in lines[1:]:
        if not line.startswith("    ") or not line.strip():
            break
        taken += 1
    end = start + len(chr(10).join(lines[:taken])) + 1
    return start, min(end, len(text))

def swap_in_yaml(text: str, out_player, in_player) -> str:
    """Replace one entry with another, leaving every comment where it was.

    The new entry goes where the old one was even when the position changes.
    §6.8 charges two transfers for a position change, so it is rare, and a
    block sitting under the wrong `# --- Defesas ---` heading is a cosmetic
    problem the printed summary points at — not a reason to refuse a legal move.
    """
    start, end = find_block(text, out_player.id)
    return text[:start] + player_block(in_player) + text[end:]


def swap_in_selection(text: str, out_id: str, in_player) -> tuple[str, str | None]:
    """Put the arrival in the departure's place on the sheet.

    Returns the new text and where he was, or None if the departing player was
    not named on the sheet at all — which is legal and needs no edit.
    """
    pattern = re.compile(
        r'^(?P<indent>\s*- )"' + re.escape(out_id) + r'"(?P<rest>.*)$', re.MULTILINE
    )
    match = pattern.search(text)
    if match is None:
        return text, None

    # Which list he was in, read from what comes before him rather than assumed.
    before = text[: match.start()]
    where = "titulares" if before.rfind("starters:") > before.rfind("bench:") else "banco"

    comment = (
        f"    # {in_player.name[:16]:<16} {POS_PT[in_player.position.value]:<4} "
        f"{in_player.club}"
    )
    line = f'{match.group("indent")}"{in_player.id}"{comment}'
    return text[: match.start()] + line + text[match.end() :], where


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sai", help="quem sai, por nome")
    parser.add_argument("--entra", help="quem entra, por nome")
    parser.add_argument("--capitao", help="mudar a braçadeira, por nome")
    parser.add_argument(
        "--jornada",
        type=int,
        help="a jornada da decisão. Sem isto usa a que está no squad.yaml",
    )
    parser.add_argument(
        "--sugerido-sai", help="quem o modelo dizia para vender, se souberes"
    )
    parser.add_argument("--sugerido-entra", help="quem o modelo dizia para comprar")
    parser.add_argument("--nota", default="", help="uma linha para o teu eu de maio")
    parser.add_argument(
        "--ensaio",
        action="store_true",
        help="mostra o que faria e não escreve nada",
    )
    args = parser.parse_args()

    if not args.sai and not args.entra and not args.capitao:
        raise Refused("não pediste nada — vê --help")
    if bool(args.sai) != bool(args.entra):
        raise Refused("uma transferência precisa de --sai e --entra")

    snapshot = ManualSquadSource(SQUAD_PATH).load()
    squad, picked = snapshot.squad, snapshot.selection
    text = SQUAD_PATH.read_text(encoding="utf-8")
    said: list[str] = []

    round_number = args.jornada or snapshot.round_number
    out_player = in_player = None

    if args.sai:
        out_player = one_of(squad.players, args.sai, "no teu plantel")
        market = [p for pos in Position for p in LigaRecordClient(timeout=60.0).search(pos)]
        in_player = one_of(market, args.entra, "no mercado")

        held = {p.id for p in squad.players}
        if in_player.id in held:
            raise Refused(f"{in_player.name} já é teu")
        if in_player.id == out_player.id:
            raise Refused("esse é o mesmo jogador")

        # §6.4 — the arithmetic, before anything is written.
        after = squad.balance() + out_player.value - in_player.value
        if after < 0:
            raise Refused(
                f"faltam {-after / 1e6:.2f}M: {in_player.name} custa "
                f"{in_player.value / 1e6:.2f}M e só libertas "
                f"{(squad.balance() + out_player.value) / 1e6:.2f}M"
            )

        # Selling the captain leaves §10.3(l) pointing at nobody. The loader
        # catches it below, but by then the message is a wrapped exception with
        # a temporary path in it — and he still would not know what to do. Say
        # it here, where the answer fits in the sentence.
        if picked is not None and picked.captain == out_player.id and not args.capitao:
            raise Refused(
                f"{out_player.name} é o teu capitão. Diz quem fica com a "
                f"braçadeira: --capitao NOME (o {in_player.name} serve, se for "
                "para os titulares)"
            )

        text = swap_in_yaml(text, out_player, in_player)
        text, where = swap_in_selection(text, out_player.id, in_player)

        said.append(
            f"  sai   {out_player.name} ({POS_PT[out_player.position.value]}, "
            f"{out_player.club}, {out_player.value / 1e6:.2f}M)"
        )
        said.append(
            f"  entra {in_player.name} ({POS_PT[in_player.position.value]}, "
            f"{in_player.club}, {in_player.value / 1e6:.2f}M)"
        )
        said.append(f"  saldo {after / 1e6:+.2f}M")
        if where:
            said.append(f"  toma o lugar dele nos {where}")
        else:
            said.append("  o que saiu não estava na folha, portanto a folha não muda")
        if out_player.position != in_player.position:
            said.append(
                f"  ATENÇÃO: muda de posição ({POS_PT[out_player.position.value]} para "
                f"{POS_PT[in_player.position.value]}). O §6.8 cobra DUAS "
                "transferências por isso, e os comentários de grupo no ficheiro "
                "ficam desalinhados."
            )

    if args.capitao:
        # Resolved against the squad as it will be, so naming the arrival works.
        pool = [p for p in squad.players if p.id != (out_player.id if out_player else None)]
        if in_player is not None:
            pool.append(in_player)
        new_captain = one_of(pool, args.capitao, "no teu plantel")
        text = re.sub(
            r'^(\s*captain:\s*)"[^"]*"(.*)$',
            lambda m: f'{m.group(1)}"{new_captain.id}"    # {new_captain.name}',
            text,
            count=1,
            flags=re.MULTILINE,
        )
        said.append(f"  capitão {new_captain.name}")

    # Nothing is written until the result passes the same checks the loader
    # runs. A squad file that fails them is worse than no change at all.
    scratch = SQUAD_PATH.with_suffix(".yaml.check")
    scratch.write_text(text, encoding="utf-8")
    try:
        checked = ManualSquadSource(scratch).load()
    except Exception as exc:  # noqa: BLE001 — any failure is a refusal
        raise Refused(f"o ficheiro resultante não carrega: {exc}") from exc
    finally:
        scratch.unlink(missing_ok=True)

    squad_check = validate_squad(checked.squad)
    if not squad_check.is_valid:
        raise Refused(
            "o plantel ficaria ilegal: "
            + "; ".join(f"{v.rule} {v.message}" for v in squad_check.violations)
        )
    if checked.selection is not None:
        sheet = validate_selection(checked.squad, checked.selection)
        if not sheet.is_valid:
            raise Refused(
                "a folha ficaria ilegal: "
                + "; ".join(f"{v.rule} {v.message}" for v in sheet.violations)
            )
        said.append(f"  formação {sheet.formation}")

    # THE LEDGER REFUSES BEFORE ANYTHING IS WRITTEN, not after.
    #
    # This ran after `SQUAD_PATH.write_text` and printed "o squad.yaml foi
    # escrito, mas o ledger recusou" — leaving the two files disagreeing about
    # what Manuel did, which is the exact failure this script exists to
    # prevent. Not hypothetical either: the scheduled job records rounds
    # through `settle_decision`, so a round can already be on file when this
    # runs.
    #
    # `record_decision` only mutates `store` in memory — `save_decisions` is
    # what touches disk — so running it up here costs nothing and turns a
    # half-applied write into an ordinary refusal, one `--ensaio` reports too.
    store = load_decisions(DECISIONS_PATH)
    try:
        record_decision(
            store,
            round_number,
            transfer_out=out_player.name if out_player else None,
            transfer_in=in_player.name if in_player else None,
            suggested_out=args.sugerido_sai,
            suggested_in=args.sugerido_entra,
            captain=args.capitao,
            note=args.nota,
        )
    except Exception as exc:  # noqa: BLE001 — any refusal is a refusal
        raise Refused(
            f"o ledger não aceita a jornada {round_number}: {exc}\n"
            "  (uma decisão já registada não se reescreve — nada foi escrito)"
        ) from exc

    print()
    print(f"JORNADA {round_number}")
    print("\n".join(said))

    if args.ensaio:
        print()
        print("  ensaio — nada foi escrito")
        return

    # Both writes, after every refusal has had its chance. Still two calls, and
    # a disk failure between them would desync the pair — but that window is
    # now a few microseconds of validated work instead of the whole ledger
    # check, and closing it properly needs a transaction neither file has.
    SQUAD_PATH.write_text(text, encoding="utf-8")
    save_decisions(DECISIONS_PATH, store)

    print()
    print(f"  escrito em {SQUAD_PATH.name} e registado na jornada {round_number}")
    print("  corre a rotina para as páginas apanharem isto")


if __name__ == "__main__":
    main()
