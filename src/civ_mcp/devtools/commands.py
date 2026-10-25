"""Typed foreground commands; translate arguments only, never own game state."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import shlex
import re

from civ_mcp.devtools.evidence import CommandError


GROUPS = {
    "unit": "List units or explicitly issue one unit command.",
    "city": "Inspect cities/production or explicitly set a production item.",
    "research": "Inspect technologies/civics or choose research.",
    "turn": "Inspect or explicitly submit/confirm one end-turn request.",
    "save": "List, create or load saves through existing lifecycle tools.",
    "knowledge": "Search static rules and inspect one entry at a chosen depth.",
    "map": "Inspect known terrain around a queried coordinate.",
    "victory": "Read current victory progress and known rival progress.",
    "policy": "Inspect government slots or explicitly assign policy cards.",
    "city-state": "Inspect known city-states or explicitly send one envoy.",
    "governor": "Inspect, appoint, assign or promote governors.",
    "congress": "Read World Congress phase, resolutions and voting options.",
    "notifications": "Read current notifications without dismissing them.",
    "government": "Inspect government/policy slots or explicitly change government.",
    "religion": "Inspect religion/pantheon beliefs or explicitly choose a pantheon.",
    "great-person": "Inspect candidates, recruit with points or patronize with gold/faith.",
    "empire": "Inspect empire yields, resource stocks and city status.",
    "era": "Inspect current era score, thresholds and dedication choices.",
    "ranking": "Inspect public score and all enabled victory rankings.",
    "civilization": "List encountered civilizations or inspect cached civilization/leader rules.",
    "technology": "Inspect live research, search cached technology unlocks/effects or select research.",
    "civic": "Inspect live civics, search cached unlocks/effects or select a civic.",
    "climate": "Read public native climate overview and known event history.",
    "history": "Read the local civilization's native historical timeline.",
    "resources": "Read the native global resource panel's public/tradable resources.",
    "session": "Read the owned Runtime identity and pending facts without game I/O.",
    "game": "Inspect the current game or explicitly launch, stop or recover it.",
    "diplomacy": "Inspect and explicitly respond to native diplomatic sessions.",
    "trade": "Inspect routes/deals or explicitly evaluate and respond to a deal.",
    "spy": "Inspect spies and explicitly select a mission or destination.",
    "popup": "Explicitly close an information popup or finished session view.",
    "developer": "Advanced diagnostics; requires civ6 debug --developer.",
    "hotseat": "Native two-human-seat experiment; requires --hotseat-seat and --hotseat-run.",
}


@dataclass(frozen=True)
class Intent:
    tool: str
    arguments: dict
    dry_run: bool = False


class HelpRequested(Exception):
    def __init__(self, document):
        self.document = document


def domain_parser(group: str):
    # Import lazily: the top-level CLI imports DebugSession only at dispatch.
    from civ_mcp.cli import Parser

    class SessionParser(Parser):
        def print_help(self, file=None):
            raise HelpRequested(self.help_document())

        def error(self, message):
            raise CommandError("INVALID_DEBUG_ARGUMENT", message,
                hint=f"In this debug session run {self.prog} --help, then correct the reported argument.")

    root = SessionParser(prog=group, description=GROUPS[group],
        epilog="Foreground session command; retains the same MCP process. Use COMMAND --help for details.")
    commands = root.add_subparsers(required=True)

    def command(name, tool, description, *, mutation=False):
        child = commands.add_parser(name, help=description, description=description,
            epilog=("Query current state first. Preview with --dry-run; actual execution sends once through Runtime. "
                    "Unknown outcomes require inspection of pending, never automatic replay." if mutation else
                    "Returns the original MCP result in a JSONL tool_result event."))
        child.set_defaults(tool=tool)
        if mutation:
            child.add_argument("--dry-run", action="store_true", help="Validate schema and emit a plan; never send the target tool")
        return child

    def coordinates(child):
        child.add_argument("--x", dest="target_x", type=int, default=argparse.SUPPRESS, help="Target X; requires --y")
        child.add_argument("--y", dest="target_y", type=int, default=argparse.SUPPRESS, help="Target Y; requires --x")

    def entity(child, name):
        child.add_argument(name, type=int, help="Native ID from this session's current state query")

    def report(name, section, description, *, pagination=True, since=False, player=False):
        child = command(name, "get_public_report", description)
        child.set_defaults(section=section)
        if pagination:
            child.add_argument("--limit", type=int, default=50)
            child.add_argument("--offset", type=int, default=0)
        if since:
            child.add_argument("--since-turn", dest="since_turn", type=int, default=0)
        if player:
            child.add_argument("--player", dest="target_player", type=int, default=-1)
        return child

    if group == "unit":
        command("list", "get_units", "List current units and native IDs.")
        move = command("move", "unit_action", "Move one unit to a queried coordinate.", mutation=True)
        entity(move, "unit_id")
        move.add_argument("target_x", type=int)
        move.add_argument("target_y", type=int)
        move.set_defaults(action="move")
        action = command("action", "unit_action", "Issue one explicit native unit action; prefer a specific unit subcommand when available.", mutation=True)
        entity(action, "unit_id")
        action.add_argument("action", help="Native action name; specific unit subcommands cover the common supported actions")
        coordinates(action)
        action.add_argument("--improvement", default=argparse.SUPPRESS, help="Improvement ID for builder actions")
        for name in ("attack", "skip", "fortify", "heal", "improve"):
            child = command(name, "unit_action", f"Explicitly {name} one unit; native eligibility is checked at execution.", mutation=True)
            entity(child, "unit_id")
            child.set_defaults(action=name)
            if name == "attack":
                child.add_argument("target_x", type=int)
                child.add_argument("target_y", type=int)
            elif name == "improve":
                child.add_argument("improvement", help="Native improvement type, for example IMPROVEMENT_FARM")
        command("skip-remaining", "skip_remaining_units", "Skip current ready units; preserve standing orders and inspect each native outcome.", mutation=True)
        for name, tool, description, mutation in (
            ("promotions", "get_unit_promotions", "Read this unit's native promotion options.", False),
            ("promote", "promote_unit", "Choose one promotion returned by unit promotions.", True),
            ("upgrade", "upgrade_unit", "Upgrade this unit; costs gold/resources and movement.", True),
            ("path", "get_pathing_estimate", "Estimate a path to a queried coordinate; does not move the unit.", False),
        ):
            child = command(name, tool, description, mutation=mutation)
            entity(child, "unit_id")
            if name == "promote":
                child.add_argument("promotion_type", help="Native promotion type from unit promotions")
            elif name == "path":
                child.add_argument("target_x", type=int)
                child.add_argument("target_y", type=int)
        command("tasks", "get_builder_tasks", "Read builder tasks and current improvements.")
        for name in ("cancel-move", "pillage", "found-city", "repair", "remove-improvement",
                     "remove-feature", "build-route", "automate", "alert", "sleep", "delete",
                     "activate", "sacrifice-charges", "spread-religion", "launch-inquisition",
                     "condemn-heretic", "remove-heresy", "trade-route", "teleport", "religious-attack"):
            description = ("Permanently disband this unit; native removal must be confirmed." if name == "delete"
                           else f"Explicitly {name} with one unit through the existing Runtime.")
            child = command(name, "unit_action", description, mutation=True)
            entity(child, "unit_id")
            child.set_defaults(action=name.replace("-", "_"))
            if name in {"trade-route", "teleport", "religious-attack"}:
                child.add_argument("target_x", type=int)
                child.add_argument("target_y", type=int)
        formations = command("formations", "get_unit_formations", "Read escort/military formation state and native eligible target IDs.")
        entity(formations, "unit_id")
        for name in ("link", "unlink", "corps", "army"):
            description = ("Merge two eligible units into a corps/army; consumes one unit." if name in {"corps", "army"}
                           else "Link or unlink an escort formation; preserves the units.")
            child = command(name, "set_unit_formation", description, mutation=True)
            entity(child, "unit_id")
            child.set_defaults(action=name)
            if name != "unlink":
                entity(child, "target_unit_id")
    elif group == "city":
        command("list", "get_cities", "List current cities and native IDs.")
        production = command("production", "get_city_production", "Read one city's available production items.")
        entity(production, "city_id")
        production = command("set-production", "set_city_production", "Choose one production item.", mutation=True)
        entity(production, "city_id")
        production.add_argument("item_type", choices=("UNIT", "BUILDING", "DISTRICT", "PROJECT"))
        production.add_argument("item_name", help="Native item ID from city production")
        coordinates(production)
        attack = command("attack", "city_action", "Fire once at a visible target; requires eligible city defenses.", mutation=True)
        entity(attack, "city_id")
        attack.add_argument("target_x", type=int)
        attack.add_argument("target_y", type=int)
        attack.set_defaults(action="attack")
        decision = command("decision", "city_action", "Explicitly keep, reject, raze or liberate a city with a current native decision.", mutation=True)
        entity(decision, "city_id")
        decision.add_argument("action", choices=("keep", "reject", "raze", "liberate_founder", "liberate_previous"))
        district = command("district", "get_district_advisor", "Read native district placement candidates and adjacency.")
        entity(district, "city_id")
        district.add_argument("district_type", help="Native district type, for example DISTRICT_CAMPUS")
        focus = command("focus", "set_city_focus", "Change citizen yield priority; default clears focus.", mutation=True)
        entity(focus, "city_id")
        focus.add_argument("focus", choices=("food", "production", "gold", "science", "culture", "faith", "default"))
        buy = command("buy", "purchase_item", "Purchase an item immediately; spends gold or faith.", mutation=True)
        entity(buy, "city_id")
        buy.add_argument("item_type", choices=("UNIT", "BUILDING"))
        buy.add_argument("item_name", help="Native item ID from city production")
        buy.add_argument("--yield", dest="yield_type", choices=("YIELD_GOLD", "YIELD_FAITH"), default="YIELD_GOLD")
        wonder = command("wonder", "get_wonder_advisor", "Read native placement candidates for one wonder.")
        entity(wonder, "city_id")
        wonder.add_argument("wonder_name")
        tiles = command("tiles", "get_purchasable_tiles", "Read purchasable tiles and native gold costs.")
        entity(tiles, "city_id")
        tile = command("buy-tile", "purchase_tile", "Buy a currently purchasable tile with gold.", mutation=True)
        entity(tile, "city_id")
        tile.add_argument("x", type=int)
        tile.add_argument("y", type=int)
    elif group == "research":
        command("show", "get_tech_civics", "Read available and current technologies/civics.")
        choose = command("set", "set_research", "Choose a technology or civic.", mutation=True)
        choose.add_argument("tech_or_civic", help="Native technology/civic ID from research show")
        choose.add_argument("--category", choices=("tech", "civic"), default="tech")
    elif group == "turn":
        command("status", "get_turn_status", "Read native turn phase and pending diagnostics.")
        command("end", "end_turn", "Submit or confirm exactly one turn request; no blocker auto-resolution.", mutation=True)
    elif group == "save":
        command("list", "list_saves", "List saves; the filesystem fallback currently scans Single, not Hotseat.")
        for name, tool, description in (
            ("create", "save_game", "Save to an independent name; existing ordinary files may be overwritten."),
            ("reload", "reload_game_save", "Return to menu and load a verified save; discards unsaved changes."),
            ("load", "load_game_save", "Load a verified save from the main menu; does not exit an active game."),
        ):
            child = command(name, tool, description, mutation=True)
            child.add_argument("save_name", help="Exact save name; keep baselines and retained checkpoints unchanged")
        index = command("load-index", "load_save", "Load a confirmed checkpoint from the latest save listing.", mutation=True)
        index.add_argument("save_index", type=int)
        menu = command("menu-load", "load_save_from_menu", "Load an explicit confirmed checkpoint through native FrontEnd.", mutation=True)
        menu.add_argument("save_name")
    elif group == "knowledge":
        search = command("search", "search_knowledge", "Search rules by name or native ID; quote queries containing spaces.")
        search.add_argument("query")
        search.add_argument("--topic", default=argparse.SUPPRESS, help="Knowledge topic; validated against the real tool schema")
        search.add_argument("--limit", type=int, default=5, help="Maximum results (1..20); default 5")
        show = command("show", "get_knowledge", "Read one static entry; does not establish game eligibility.")
        show.add_argument("entry_id")
        show.add_argument("--level", choices=("summary", "rule", "evidence"), default="summary")
    elif group == "map":
        area = command("area", "get_map_area", "Read known terrain; radius is 1..4 without silent clamping.")
        area.add_argument("center_x", type=int)
        area.add_argument("center_y", type=int)
        area.add_argument("--radius", type=int, choices=range(1, 5), default=2)
        settle = command("settle", "get_settle_advisor", "Read settlement candidates for a currently queried settler.")
        entity(settle, "unit_id")
        command("settle-global", "get_global_settle_advisor", "Read known global settlement candidates.")
        command("strategic", "get_strategic_map", "Read the visible strategic map; hidden information stays unknown.")
    elif group == "victory":
        command("show", "get_victory_progress", "Read victory progress; hidden rival facts remain unknown.")
    elif group == "policy":
        command("show", "get_policies", "Read government slots and available policy cards.")
        assign = command("set", "set_policies", "Assign explicit SLOT=POLICY_ID pairs; NONE clears a slot, omitted slots stay unchanged.", mutation=True)
        assign.add_argument("assignments", nargs="+", help="Pairs separated by spaces or commas; for example 0=POLICY_AGOGE 1=NONE")
    elif group == "city-state":
        command("list", "get_city_states", "Read known city-states, player IDs, envoys and suzerains.")
        envoy = command("envoy", "send_envoy", "Send one envoy; consumes one available token when confirmed.", mutation=True)
        envoy.add_argument("player_id", type=int, help="City-state player ID from city-state list")
    elif group == "governor":
        command("list", "get_governors", "Read governors, titles, assignments and native options.")
        for name, tool, description in (
            ("appoint", "appoint_governor", "Appoint one governor; spends an available title."),
            ("assign", "assign_governor", "Reassign one appointed governor to an identified city."),
            ("promote", "promote_governor", "Choose one promotion; spends an available title."),
        ):
            child = command(name, tool, description, mutation=True)
            child.add_argument("governor_type", help="Native governor type from governor list")
            if name == "assign":
                entity(child, "city_id")
                child.add_argument("--owner", dest="target_owner", type=int, default=argparse.SUPPRESS,
                                   help="Destination player ID (0..63); omit for the local player")
            elif name == "promote":
                child.add_argument("promotion_type", help="Native governor promotion from governor list")
    elif group == "congress":
        command("show", "get_world_congress", "Read native stage and choices; never opens voting or submits votes.")
        command("open", "open_world_congress", "Explicitly enter a verified native voting stage.", mutation=True)
        votes = command("votes", "queue_wc_votes", "Register or submit a complete explicit JSON vote plan; unknown never replays.", mutation=True)
        votes.add_argument("votes", help="One quoted JSON array; inspect congress show for native identifiers and costs")
    elif group == "notifications":
        command("show", "get_notifications", "Read notifications; never dismisses popups or chooses responses.")
    elif group == "government":
        command("show", "get_policies", "Read current government and policy slots.")
        report("list", "governments", "Read currently unlocked governments, slots and native change eligibility.")
        change = command("change", "change_government", "Choose an unlocked government explicitly.", mutation=True)
        change.add_argument("government_type")
    elif group == "religion":
        command("show", "get_religion_defense", "Read religion majority, pressure and visible religious threats.")
        command("pantheon", "get_pantheon_beliefs", "Read current pantheon, faith threshold and available bonuses.")
        command("beliefs", "get_religion_beliefs", "Read founded religion and available belief choices.")
        choose = command("choose-pantheon", "choose_pantheon", "Choose one available pantheon bonus; native eligibility required.", mutation=True)
        choose.add_argument("belief_type")
        found = command("found", "found_religion", "Found a religion using explicitly selected native religion and belief IDs.", mutation=True)
        for name in ("religion_type", "follower_belief", "founder_belief"):
            found.add_argument(name)
        command("spread", "get_religion_spread", "Read religion breakdown across currently visible cities.")
    elif group == "great-person":
        command("list", "get_great_people", "Read candidates, points and gold/faith patronage costs.")
        advisor = command("advisor", "get_gp_advisor", "Read native activation-city suggestions for a Great Person unit.")
        entity(advisor, "unit_index")
        for name, tool in [("recruit", "recruit_great_person"), ("buy", "patronize_great_person"), ("reject", "reject_great_person")]:
            child = command(name, tool, "Explicitly select one current Great Person; Runtime retains unknown outcomes.", mutation=True)
            entity(child, "individual_id")
            if name == "buy":
                child.add_argument("--currency", choices=("gold", "faith"), default="gold")
    elif group == "empire":
        report("show", "empire", "Read per-turn science/culture/faith/gold, favor and available envoys.", pagination=False)
        command("resources", "get_empire_resources", "Read known resource stocks, accumulation and consumption.")
        command("cities", "get_cities", "Read owned city status, yields, production and visible needs.")
    elif group == "era":
        command("show", "get_game_overview", "Read current era, age, era score and next-era thresholds.")
        command("dedications", "get_dedications", "Read current selectable dedication bonuses.")
        choose = command("choose", "choose_dedication", "Choose an index returned by era dedications.", mutation=True)
        choose.add_argument("dedication_index", type=int)
    elif group == "ranking":
        command("show", "get_victory_progress", "Read public score, science, culture, domination, religion and diplomacy progress.")
    elif group == "civilization":
        command("list", "get_diplomacy", "Read encountered civilizations, leaders and current relationships.")
        ability = command("abilities", "get_knowledge", "Read a cached civilization/leader rule; current activation remains unverified.")
        ability.add_argument("entry_id", help="Civilization/leader ID from current civilization list and knowledge search")
        ability.add_argument("--level", choices=("summary", "rule", "evidence"), default="rule")
    elif group in {"technology", "civic"}:
        from civ_mcp.research_catalog import FILTERS, ALIASES
        command("status", "get_tech_civics", "Read current research, civic progress and available live choices.")
        select = command("set", "set_research", "Select one currently available research target.", mutation=True)
        select.add_argument("tech_or_civic")
        select.set_defaults(category="tech" if group == "technology" else "civic")
        search = command("search", "search_research", "Search cached names, unlocks and positive effect associations; no live eligibility claim.")
        search.add_argument("query", nargs="?", default="")
        search.set_defaults(kind=group)
        search.add_argument("--filter", dest="filters", choices=tuple(FILTERS)+tuple(ALIASES), action="append", default=None)
        search.add_argument("--match", choices=("any", "all"), default="any")
        search.add_argument("--limit", type=int, default=20)
        search.add_argument("--offset", type=int, default=0)
    elif group == "climate":
        report("show", "climate", "Read native carbon footprint and climate; no physical ppm inference.", pagination=False)
        report("history", "climate-history", "Read global or revealed-location events, as in the native climate UI.", since=True)
    elif group == "history":
        report("show", "history", "Read local historic moments, descriptions, turns and era score.", since=True)
    elif group == "resources":
        report("global", "world-resources", "Read own stocks and known civilizations' native tradable resources.", player=True)
    elif group == "session":
        command("status", "get_runtime_status", "Read this owner's in-memory pending and process identity; no game access.")
    elif group == "game":
        command("snapshot", "get_game_snapshot", "Read a consistent current-player snapshot; refuses any pending.")
        command("launch", "launch_game", "Explicitly launch Civ6; does not choose or load a save.", mutation=True)
        command("menu", "return_to_main_menu", "Return to the native main menu; keeps the process, never loads or saves.", mutation=True)
        command("stop", "kill_game", "Explicitly stop Civ6; pending lifecycle gates still apply.", mutation=True)
        recovery = command("recover", "restart_and_load", "Explicit fault recovery: restart Civ6 and load a confirmed checkpoint.", mutation=True)
        recovery.add_argument("save_name")
    elif group == "diplomacy":
        command("list", "get_diplomacy", "Read encountered civilizations and native diplomatic actions.")
        command("pending", "get_pending_diplomacy", "Read current statements and verified response choices.")
        for name, tool in (("respond", "respond_to_diplomacy"), ("close", "close_diplomacy_session"),
                           ("action", "send_diplomatic_action"), ("peace", "propose_peace"), ("alliance", "form_alliance")):
            child = command(name, tool, "Explicit native diplomacy; inspect current options, then retain any unknown request.", mutation=True)
            entity(child, "other_player_id")
            if name == "respond":
                child.add_argument("response", help="Exact current native choice from diplomacy pending")
            elif name == "action":
                child.add_argument("action", help="Native action from diplomacy list or trade options")
            elif name == "alliance":
                child.add_argument("--type", dest="alliance_type", choices=("MILITARY", "RESEARCH", "CULTURAL", "ECONOMIC", "RELIGIOUS"), default="MILITARY")
    elif group == "trade":
        command("pending", "get_pending_trades", "Read incoming terms and their native fingerprint before evaluating/responding.")
        command("routes", "get_trade_routes", "Read active routes and current route capacity.")
        destinations = command("destinations", "get_trade_destinations", "Read this trader's currently valid destination coordinates.")
        entity(destinations, "unit_id")
        options = command("options", "get_trade_options", "Read native deal/relationship options for an encountered civilization.")
        entity(options, "other_player_id")
        response = command("respond", "respond_to_trade", "Explicitly accept or reject current verified terms; unknown retains the original terms.", mutation=True)
        entity(response, "other_player_id")
        choice = response.add_mutually_exclusive_group(required=True)
        choice.add_argument("--accept", dest="accept", action="store_true")
        choice.add_argument("--reject", dest="accept", action="store_false")
        negotiate = command("negotiate", "negotiate_incoming_trade", "Evaluate/edit existing incoming gold entries; never concludes a deal.", mutation=True)
        entity(negotiate, "other_player_id")
        negotiate.add_argument("--fingerprint", dest="expected_fingerprint", required=True)
        negotiate.add_argument("--mode", choices=("inspect", "equalize"), default="inspect")
        for name in ("our_gold", "their_gold", "our_gpt", "their_gpt"):
            negotiate.add_argument("--" + name.replace("_", "-"), dest=name, type=int, default=argparse.SUPPRESS)
        propose = command("propose", "propose_trade", "Explicitly preview or send a complete AI-country deal; human seats are unsupported.", mutation=True)
        entity(propose, "other_player_id")
        propose.add_argument("--mode", choices=("test", "send"), default="send")
        for side in ("offer", "request"):
            for field in ("gold", "gold_per_turn", "favor"):
                name = side + "_" + field
                propose.add_argument("--" + name.replace("_", "-"), dest=name, type=int, default=0)
            propose.add_argument("--" + side + "-resources", dest=side + "_resources", default="")
            propose.add_argument("--" + side + "-open-borders", dest=side + "_open_borders", action="store_true")
        propose.add_argument("--joint-war-target", type=int, default=0)
        propose.add_argument("--joint-war-type", type=int, default=argparse.SUPPRESS)
    elif group == "spy":
        command("list", "get_spies", "Read spies and their current missions.")
        action = command("action", "spy_action", "Explicitly assign a native spy action and destination.", mutation=True)
        entity(action, "unit_id")
        action.add_argument("action", help="Native mission/action from spy list")
        action.add_argument("target_x", type=int)
        action.add_argument("target_y", type=int)
    elif group == "popup":
        command("dismiss", "dismiss_popup", "Close verified information or a finished-session view; never chooses diplomatic terms.", mutation=True)
    elif group == "developer":
        lua = command("lua", "run_lua", "Run arbitrary diagnostic Lua only in an explicitly enabled developer session.", mutation=True)
        lua.add_argument("code")
        lua.add_argument("--context", choices=("gamecore", "ingame"), default="gamecore")
    elif group == "hotseat":
        command("begin", "begin_hotseat_turn", "Explicitly confirm the bound native human seat's PlayerChange.", mutation=True)
        command("end", "end_hotseat_turn", "Submit/confirm native handoff for the bound seat; does not use normal end_turn.", mutation=True)
        batch = command("actions", "hotseat_actions", "Execute a quoted JSON list of independent explicit actions; inspect every receipt.", mutation=True)
        batch.add_argument("actions_json")
    if group == "great-person":
        report("history", "great-people-history", "Read native past recruitment; currency/payment may be unknown.", player=True)
    elif group == "empire":
        report("gossip", "gossip", "Read only native visible gossip about encountered civilizations.", since=True, player=True)
    elif group == "civilization":
        show = report("show", "encountered", "Read an encountered civilization/leader with cached ability rules.", pagination=False)
        entity(show, "target_player")
        show.set_defaults(include_rules=True)
    elif group == "map":
        from civ_mcp.lua.public_reports import MAP_KINDS
        search = report("search", "map-search", "Locate visible villages and revealed, resource-visible deposits/sites.")
        search.add_argument("search_kind", choices=sorted(MAP_KINDS))
        search.add_argument("--query", default="", help="Localized name or native resource type fragment")
    return root


def parse_command(line: str) -> Intent:
    try:
        words = shlex.split(line)
    except ValueError as exc:
        raise CommandError("INVALID_DEBUG_ARGUMENT", str(exc),
            hint="Close the quoted argument; use double quotes around names with spaces. No shell expansion is performed.") from exc
    values = vars(domain_parser(words[0]).parse_args(words[1:]))
    tool, dry_run = values.pop("tool"), values.pop("dry_run", False)
    if ("target_x" in values) != ("target_y" in values):
        raise CommandError("INVALID_DEBUG_ARGUMENT", "Target coordinates must include both --x and --y",
                           hint=f"Use {words[0]} COMMAND --help and supply both coordinates or neither.")
    if tool == "set_policies":
        pairs = re.split(r"\s*,\s*|\s+", " ".join(values["assignments"]).strip())
        slots = set()
        normalized = []
        for pair in pairs:
            match = re.fullmatch(r"([0-9]+)=(POLICY_[A-Z0-9_]+|NONE)", pair.strip())
            if match is None or int(match[1]) in slots:
                raise CommandError("INVALID_DEBUG_ARGUMENT", f"Malformed or duplicate policy slot assignment: {pair}",
                    hint="Use policy show, then policy set SLOT=POLICY_ID [SLOT=NONE] --dry-run; assign each slot only once.")
            slots.add(int(match[1]))
            normalized.append(f"{int(match[1])}={match[2]}")
        values["assignments"] = ",".join(normalized)
    if tool == "patronize_great_person":
        values["yield_type"] = "YIELD_" + values.pop("currency").upper()
    return Intent(tool, values, dry_run)
