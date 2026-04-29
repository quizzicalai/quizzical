"""Built-in reviewed canonical catalog for bounded quiz taxonomies.

The catalog is organized into 10 themed expansion passes so additions remain
auditable. Each set is intended to represent a closed, factual taxonomy with a
stable membership list that is safe to use for canonical preprocessing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _lines(raw: str) -> list[str]:
    return [line.strip() for line in raw.strip().splitlines() if line.strip()]


def _entry(names: Sequence[str], aliases: Sequence[str] = ()) -> dict[str, Any]:
    clean_names = [str(name).strip() for name in names if str(name).strip()]
    clean_aliases = [str(alias).strip() for alias in aliases if str(alias).strip()]
    return {
        "names": clean_names,
        "count_hint": len(clean_names),
        "aliases": clean_aliases,
    }


def _hierarchical_team_sets(
    prefix: str,
    groups: Mapping[str, Mapping[str, Sequence[str]]],
    *,
    aliases: Sequence[str] = (),
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}
    league_teams: list[str] = []

    for group_name, subgroups in groups.items():
        group_teams: list[str] = []
        for subgroup_name, names in subgroups.items():
            clean_names = [str(name).strip() for name in names if str(name).strip()]
            catalog[f"{prefix} {subgroup_name} Teams"] = _entry(clean_names)
            group_teams.extend(clean_names)
            league_teams.extend(clean_names)
        catalog[f"{prefix} {group_name} Teams"] = _entry(group_teams)

    catalog[f"{prefix} Teams"] = _entry(league_teams, aliases=aliases)
    return catalog


def _merge_passes(*passes: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    sets: dict[str, dict[str, Any]] = {}
    aliases: dict[str, list[str]] = {}

    for catalog_pass in passes:
        for title, entry in catalog_pass.items():
            names = [str(name).strip() for name in entry.get("names", []) if str(name).strip()]
            sets[title] = {
                "names": names,
                "count_hint": int(entry.get("count_hint") or len(names)),
            }

            alias_list = [str(alias).strip() for alias in entry.get("aliases", []) if str(alias).strip()]
            if alias_list:
                aliases[title] = alias_list

    return {"sets": sets, "aliases": aliases}


# Pass 1: personality systems, communication models, and behavior frameworks.
PASS_1_PERSONALITY_FRAMEWORKS = {
    "DISC Styles": _entry(
        ["Dominance", "Influence", "Steadiness", "Conscientiousness"],
        aliases=("disc", "disc profiles", "disc styles"),
    ),
    "Five Love Languages": _entry(
        [
            "Words of Affirmation",
            "Acts of Service",
            "Receiving Gifts",
            "Quality Time",
            "Physical Touch",
        ],
        aliases=("love language", "love languages"),
    ),
    "Attachment Styles": _entry(
        ["Secure", "Anxious-Preoccupied", "Dismissive-Avoidant", "Fearful-Avoidant"],
        aliases=("attachment style", "adult attachment styles"),
    ),
    "Thomas-Kilmann Conflict Modes": _entry(
        ["Competing", "Collaborating", "Compromising", "Avoiding", "Accommodating"],
        aliases=("conflict modes", "conflict styles", "thomas kilmann"),
    ),
    "VARK Learning Modalities": _entry(
        ["Visual", "Auditory", "Reading/Writing", "Kinesthetic"],
        aliases=("vark", "learning styles", "learning modalities"),
    ),
    "Holland Codes": _entry(
        ["Realistic", "Investigative", "Artistic", "Social", "Enterprising", "Conventional"],
        aliases=("riasec", "career personality types", "holland types"),
    ),
    "Keirsey Temperaments": _entry(
        ["Artisan", "Guardian", "Idealist", "Rational"],
        aliases=("keirsey", "keirsey types", "keirsey temperaments"),
    ),
    "Jungian Cognitive Functions": _entry(
        [
            "Extraverted Thinking",
            "Introverted Thinking",
            "Extraverted Feeling",
            "Introverted Feeling",
            "Extraverted Sensing",
            "Introverted Sensing",
            "Extraverted Intuition",
            "Introverted Intuition",
        ],
        aliases=("cognitive functions", "jungian functions"),
    ),
    "CliftonStrengths Domains": _entry(
        ["Executing", "Influencing", "Relationship Building", "Strategic Thinking"],
        aliases=("strengths domains", "clifton strengths domains"),
    ),
    "Four Temperaments": _entry(
        ["Sanguine", "Choleric", "Melancholic", "Phlegmatic"],
        aliases=("temperaments", "humoral temperaments"),
    ),
    "Leadership Compass Points": _entry(
        ["North", "East", "South", "West"],
        aliases=("leadership compass",),
    ),
    "Stress Responses": _entry(
        ["Fight", "Flight", "Freeze", "Fawn"],
        aliases=("fight flight freeze fawn", "trauma responses"),
    ),
}


# Pass 2: fantasy, gaming, and fandom taxonomies with stable membership.
PASS_2_FANTASY_AND_FANDOM = {
    "Pokemon Types": _entry(
        [
            "Normal",
            "Fire",
            "Water",
            "Electric",
            "Grass",
            "Ice",
            "Fighting",
            "Poison",
            "Ground",
            "Flying",
            "Psychic",
            "Bug",
            "Rock",
            "Ghost",
            "Dragon",
            "Dark",
            "Steel",
            "Fairy",
        ],
        aliases=("pokemon type", "pokémon type", "pokémon types"),
    ),
    "Pokemon Regions": _entry(
        ["Kanto", "Johto", "Hoenn", "Sinnoh", "Unova", "Kalos", "Alola", "Galar", "Paldea"],
        aliases=("pokemon region", "pokémon regions"),
    ),
    "Eeveelutions": _entry(
        ["Vaporeon", "Jolteon", "Flareon", "Espeon", "Umbreon", "Leafeon", "Glaceon", "Sylveon"],
        aliases=("eeveelution",),
    ),
    "Ilvermorny Houses": _entry(
        ["Horned Serpent", "Wampus", "Thunderbird", "Pukwudgie"],
        aliases=("ilvermorny house", "ilvermorny"),
    ),
    "Divergent Factions": _entry(
        ["Abnegation", "Amity", "Candor", "Dauntless", "Erudite"],
        aliases=("divergent faction",),
    ),
    "Avatar Nations": _entry(
        ["Air Nomads", "Water Tribes", "Earth Kingdom", "Fire Nation"],
        aliases=("avatar nation", "four nations", "atla nations"),
    ),
    "Bending Disciplines": _entry(
        ["Airbending", "Waterbending", "Earthbending", "Firebending"],
        aliases=("bending types", "avatar bending"),
    ),
    "Percy Jackson Cabins": _entry(
        [
            "Zeus",
            "Hera",
            "Poseidon",
            "Demeter",
            "Athena",
            "Apollo",
            "Ares",
            "Aphrodite",
            "Hephaestus",
            "Hermes",
            "Dionysus",
            "Hades",
        ],
        aliases=("camp half-blood cabins", "olympian cabins"),
    ),
    "Magic The Gathering Colors": _entry(
        ["White", "Blue", "Black", "Red", "Green"],
        aliases=("mtg colors", "mana colors", "magic colors"),
    ),
    "Ravnica Guilds": _entry(
        [
            "Azorius Senate",
            "House Dimir",
            "Cult of Rakdos",
            "Gruul Clans",
            "Selesnya Conclave",
            "Orzhov Syndicate",
            "Izzet League",
            "Golgari Swarm",
            "Boros Legion",
            "Simic Combine",
        ],
        aliases=("mtg guilds", "guilds of ravnica"),
    ),
    "DND Classes": _entry(
        [
            "Barbarian",
            "Bard",
            "Cleric",
            "Druid",
            "Fighter",
            "Monk",
            "Paladin",
            "Ranger",
            "Rogue",
            "Sorcerer",
            "Warlock",
            "Wizard",
        ],
        aliases=("dnd classes", "d&d classes", "dungeons and dragons classes"),
    ),
    "DND Schools of Magic": _entry(
        [
            "Abjuration",
            "Conjuration",
            "Divination",
            "Enchantment",
            "Evocation",
            "Illusion",
            "Necromancy",
            "Transmutation",
        ],
        aliases=("dnd schools of magic", "d&d schools of magic", "schools of magic"),
    ),
    "DND Conditions": _entry(
        [
            "Blinded",
            "Charmed",
            "Deafened",
            "Exhaustion",
            "Frightened",
            "Grappled",
            "Incapacitated",
            "Invisible",
            "Paralyzed",
            "Petrified",
            "Poisoned",
            "Prone",
            "Restrained",
            "Stunned",
            "Unconscious",
        ],
        aliases=("dnd conditions", "d&d conditions", "conditions in 5e"),
    ),
    "Great Houses of Westeros": _entry(
        ["Stark", "Lannister", "Baratheon", "Targaryen", "Greyjoy", "Martell", "Tyrell", "Arryn", "Tully"],
        aliases=("game of thrones houses", "westeros houses"),
    ),
    "Hunger Games Districts": _entry(
        [
            "District 1",
            "District 2",
            "District 3",
            "District 4",
            "District 5",
            "District 6",
            "District 7",
            "District 8",
            "District 9",
            "District 10",
            "District 11",
            "District 12",
            "District 13",
        ],
        aliases=("hunger games district",),
    ),
    "Deathly Hallows": _entry(
        ["Elder Wand", "Resurrection Stone", "Invisibility Cloak"],
        aliases=("deathly hallow",),
    ),
    "Hogwarts Core Subjects": _entry(
        [
            "Astronomy",
            "Charms",
            "Defense Against the Dark Arts",
            "Herbology",
            "History of Magic",
            "Potions",
            "Transfiguration",
        ],
        aliases=("hogwarts subjects",),
    ),
}


US_STATES = _lines(
    """
    Alabama
    Alaska
    Arizona
    Arkansas
    California
    Colorado
    Connecticut
    Delaware
    Florida
    Georgia
    Hawaii
    Idaho
    Illinois
    Indiana
    Iowa
    Kansas
    Kentucky
    Louisiana
    Maine
    Maryland
    Massachusetts
    Michigan
    Minnesota
    Mississippi
    Missouri
    Montana
    Nebraska
    Nevada
    New Hampshire
    New Jersey
    New Mexico
    New York
    North Carolina
    North Dakota
    Ohio
    Oklahoma
    Oregon
    Pennsylvania
    Rhode Island
    South Carolina
    South Dakota
    Tennessee
    Texas
    Utah
    Vermont
    Virginia
    Washington
    West Virginia
    Wisconsin
    Wyoming
    """
)

US_STATE_CAPITALS = _lines(
    """
    Montgomery
    Juneau
    Phoenix
    Little Rock
    Sacramento
    Denver
    Hartford
    Dover
    Tallahassee
    Atlanta
    Honolulu
    Boise
    Springfield
    Indianapolis
    Des Moines
    Topeka
    Frankfort
    Baton Rouge
    Augusta
    Annapolis
    Boston
    Lansing
    Saint Paul
    Jackson
    Jefferson City
    Helena
    Lincoln
    Carson City
    Concord
    Trenton
    Santa Fe
    Albany
    Raleigh
    Bismarck
    Columbus
    Oklahoma City
    Salem
    Harrisburg
    Providence
    Columbia
    Pierre
    Nashville
    Austin
    Salt Lake City
    Montpelier
    Richmond
    Olympia
    Charleston
    Madison
    Cheyenne
    """
)

US_STATE_ABBREVIATIONS = _lines(
    """
    AL
    AK
    AZ
    AR
    CA
    CO
    CT
    DE
    FL
    GA
    HI
    ID
    IL
    IN
    IA
    KS
    KY
    LA
    ME
    MD
    MA
    MI
    MN
    MS
    MO
    MT
    NE
    NV
    NH
    NJ
    NM
    NY
    NC
    ND
    OH
    OK
    OR
    PA
    RI
    SC
    SD
    TN
    TX
    UT
    VT
    VA
    WA
    WV
    WI
    WY
    """
)

US_CENSUS_REGIONS = {
    "Northeast States": _lines(
        """
        Connecticut
        Maine
        Massachusetts
        New Hampshire
        Rhode Island
        Vermont
        New Jersey
        New York
        Pennsylvania
        """
    ),
    "Midwest States": _lines(
        """
        Illinois
        Indiana
        Michigan
        Ohio
        Wisconsin
        Iowa
        Kansas
        Minnesota
        Missouri
        Nebraska
        North Dakota
        South Dakota
        """
    ),
    "South States": _lines(
        """
        Delaware
        Florida
        Georgia
        Maryland
        North Carolina
        South Carolina
        Virginia
        District of Columbia
        West Virginia
        Alabama
        Kentucky
        Mississippi
        Tennessee
        Arkansas
        Louisiana
        Oklahoma
        Texas
        """
    ),
    "West States": _lines(
        """
        Arizona
        Colorado
        Idaho
        Montana
        Nevada
        New Mexico
        Utah
        Wyoming
        Alaska
        California
        Hawaii
        Oregon
        Washington
        """
    ),
}

US_CENSUS_DIVISIONS = {
    "New England States": _lines("""Connecticut
    Maine
    Massachusetts
    New Hampshire
    Rhode Island
    Vermont"""),
    "Middle Atlantic States": _lines("""New Jersey
    New York
    Pennsylvania"""),
    "East North Central States": _lines("""Illinois
    Indiana
    Michigan
    Ohio
    Wisconsin"""),
    "West North Central States": _lines("""Iowa
    Kansas
    Minnesota
    Missouri
    Nebraska
    North Dakota
    South Dakota"""),
    "South Atlantic States": _lines("""Delaware
    Florida
    Georgia
    Maryland
    North Carolina
    South Carolina
    Virginia
    District of Columbia
    West Virginia"""),
    "East South Central States": _lines("""Alabama
    Kentucky
    Mississippi
    Tennessee"""),
    "West South Central States": _lines("""Arkansas
    Louisiana
    Oklahoma
    Texas"""),
    "Mountain States": _lines("""Arizona
    Colorado
    Idaho
    Montana
    Nevada
    New Mexico
    Utah
    Wyoming"""),
    "Pacific States": _lines("""Alaska
    California
    Hawaii
    Oregon
    Washington"""),
}


# Pass 3: geography, civics, and stable regional groupings.
PASS_3_GEOGRAPHY_AND_CIVICS = {
    "US States": _entry(US_STATES, aliases=("u.s. states", "american states", "50 states")),
    "US State Capitals": _entry(US_STATE_CAPITALS, aliases=("u.s. state capitals", "state capitals")),
    "US State Abbreviations": _entry(US_STATE_ABBREVIATIONS, aliases=("postal abbreviations", "state abbreviations")),
    "US Territories": _entry(
        ["American Samoa", "Guam", "Northern Mariana Islands", "Puerto Rico", "US Virgin Islands"],
        aliases=("u.s. territories", "american territories"),
    ),
    "Continents": _entry(["Africa", "Antarctica", "Asia", "Europe", "North America", "South America", "Oceania"]),
    "Oceans": _entry(["Arctic", "Atlantic", "Indian", "Pacific", "Southern"]),
    "Great Lakes": _entry(["Superior", "Michigan", "Huron", "Erie", "Ontario"]),
    "Canadian Provinces": _entry(
        [
            "Alberta",
            "British Columbia",
            "Manitoba",
            "New Brunswick",
            "Newfoundland and Labrador",
            "Nova Scotia",
            "Ontario",
            "Prince Edward Island",
            "Quebec",
            "Saskatchewan",
        ],
        aliases=("canada provinces",),
    ),
    "Canadian Territories": _entry(["Northwest Territories", "Nunavut", "Yukon"], aliases=("canada territories",)),
    "Canadian Provinces and Territories": _entry(
        [
            "Alberta",
            "British Columbia",
            "Manitoba",
            "New Brunswick",
            "Newfoundland and Labrador",
            "Nova Scotia",
            "Ontario",
            "Prince Edward Island",
            "Quebec",
            "Saskatchewan",
            "Northwest Territories",
            "Nunavut",
            "Yukon",
        ],
        aliases=("canadian provinces and territories",),
    ),
    "Canadian Provincial Capitals": _entry(
        ["Edmonton", "Victoria", "Winnipeg", "Fredericton", "Saint John's", "Halifax", "Toronto", "Charlottetown", "Quebec City", "Regina"]
    ),
    "Canadian Territorial Capitals": _entry(["Yellowknife", "Iqaluit", "Whitehorse"]),
    "EU Member States": _entry(
        [
            "Austria",
            "Belgium",
            "Bulgaria",
            "Croatia",
            "Cyprus",
            "Czechia",
            "Denmark",
            "Estonia",
            "Finland",
            "France",
            "Germany",
            "Greece",
            "Hungary",
            "Ireland",
            "Italy",
            "Latvia",
            "Lithuania",
            "Luxembourg",
            "Malta",
            "Netherlands",
            "Poland",
            "Portugal",
            "Romania",
            "Slovakia",
            "Slovenia",
            "Spain",
            "Sweden",
        ],
        aliases=("european union countries", "eu countries"),
    ),
    "G7 Countries": _entry(["Canada", "France", "Germany", "Italy", "Japan", "United Kingdom", "United States"]),
}

for title, names in US_CENSUS_REGIONS.items():
    PASS_3_GEOGRAPHY_AND_CIVICS[title] = _entry(names)

for title, names in US_CENSUS_DIVISIONS.items():
    PASS_3_GEOGRAPHY_AND_CIVICS[title] = _entry(names)


def _build_element_catalog(
    elements: Sequence[tuple[str, int, str, str]],
) -> dict[str, dict[str, Any]]:
    catalog = {
        "Periodic Table Elements": _entry(
            [name for name, _, _, _ in elements],
            aliases=("chemical elements", "periodic table", "elements"),
        )
    }

    by_period: dict[int, list[str]] = {}
    by_block: dict[str, list[str]] = {}
    by_category: dict[str, list[str]] = {}
    metals: list[str] = []
    nonmetals: list[str] = []

    metal_categories = {
        "Alkali Metals",
        "Alkaline Earth Metals",
        "Other Metals",
        "Lanthanides",
        "Actinides",
    }
    nonmetal_categories = {"Other Nonmetals", "Halogens", "Noble Gases"}

    for name, period, block, category in elements:
        by_period.setdefault(period, []).append(name)
        by_block.setdefault(block, []).append(name)
        by_category.setdefault(category, []).append(name)

        if category in metal_categories:
            metals.append(name)
        elif category in nonmetal_categories:
            nonmetals.append(name)

    for period, names in sorted(by_period.items()):
        catalog[f"Period {period} Elements"] = _entry(names)

    block_titles = {
        "s": "s-Block Elements",
        "p": "p-Block Elements",
        "d": "d-Block Elements",
        "f": "f-Block Elements",
    }
    for block, title in block_titles.items():
        catalog[title] = _entry(by_block[block])

    category_titles = {
        "Alkali Metals": "Alkali Metals",
        "Alkaline Earth Metals": "Alkaline Earth Metals",
        "Other Metals": "Other Metal Elements",
        "Metalloids": "Metalloid Elements",
        "Other Nonmetals": "Other Nonmetal Elements",
        "Halogens": "Halogen Elements",
        "Noble Gases": "Noble Gas Elements",
        "Lanthanides": "Lanthanide Elements",
        "Actinides": "Actinide Elements",
    }
    for category, title in category_titles.items():
        catalog[title] = _entry(by_category[category])

    catalog["Metal Elements"] = _entry(metals)
    catalog["Nonmetal Elements"] = _entry(nonmetals)
    return catalog


def _tarot_suit_cards(suit: str) -> list[str]:
    return [
        f"Ace of {suit}",
        f"Two of {suit}",
        f"Three of {suit}",
        f"Four of {suit}",
        f"Five of {suit}",
        f"Six of {suit}",
        f"Seven of {suit}",
        f"Eight of {suit}",
        f"Nine of {suit}",
        f"Ten of {suit}",
        f"Page of {suit}",
        f"Knight of {suit}",
        f"Queen of {suit}",
        f"King of {suit}",
    ]


PERIODIC_TABLE_ELEMENTS: list[tuple[str, int, str, str]] = [
    ("Hydrogen", 1, "s", "Other Nonmetals"),
    ("Helium", 1, "s", "Noble Gases"),
    ("Lithium", 2, "s", "Alkali Metals"),
    ("Beryllium", 2, "s", "Alkaline Earth Metals"),
    ("Boron", 2, "p", "Metalloids"),
    ("Carbon", 2, "p", "Other Nonmetals"),
    ("Nitrogen", 2, "p", "Other Nonmetals"),
    ("Oxygen", 2, "p", "Other Nonmetals"),
    ("Fluorine", 2, "p", "Halogens"),
    ("Neon", 2, "p", "Noble Gases"),
    ("Sodium", 3, "s", "Alkali Metals"),
    ("Magnesium", 3, "s", "Alkaline Earth Metals"),
    ("Aluminum", 3, "p", "Other Metals"),
    ("Silicon", 3, "p", "Metalloids"),
    ("Phosphorus", 3, "p", "Other Nonmetals"),
    ("Sulfur", 3, "p", "Other Nonmetals"),
    ("Chlorine", 3, "p", "Halogens"),
    ("Argon", 3, "p", "Noble Gases"),
    ("Potassium", 4, "s", "Alkali Metals"),
    ("Calcium", 4, "s", "Alkaline Earth Metals"),
    ("Scandium", 4, "d", "Other Metals"),
    ("Titanium", 4, "d", "Other Metals"),
    ("Vanadium", 4, "d", "Other Metals"),
    ("Chromium", 4, "d", "Other Metals"),
    ("Manganese", 4, "d", "Other Metals"),
    ("Iron", 4, "d", "Other Metals"),
    ("Cobalt", 4, "d", "Other Metals"),
    ("Nickel", 4, "d", "Other Metals"),
    ("Copper", 4, "d", "Other Metals"),
    ("Zinc", 4, "d", "Other Metals"),
    ("Gallium", 4, "p", "Other Metals"),
    ("Germanium", 4, "p", "Metalloids"),
    ("Arsenic", 4, "p", "Metalloids"),
    ("Selenium", 4, "p", "Other Nonmetals"),
    ("Bromine", 4, "p", "Halogens"),
    ("Krypton", 4, "p", "Noble Gases"),
    ("Rubidium", 5, "s", "Alkali Metals"),
    ("Strontium", 5, "s", "Alkaline Earth Metals"),
    ("Yttrium", 5, "d", "Other Metals"),
    ("Zirconium", 5, "d", "Other Metals"),
    ("Niobium", 5, "d", "Other Metals"),
    ("Molybdenum", 5, "d", "Other Metals"),
    ("Technetium", 5, "d", "Other Metals"),
    ("Ruthenium", 5, "d", "Other Metals"),
    ("Rhodium", 5, "d", "Other Metals"),
    ("Palladium", 5, "d", "Other Metals"),
    ("Silver", 5, "d", "Other Metals"),
    ("Cadmium", 5, "d", "Other Metals"),
    ("Indium", 5, "p", "Other Metals"),
    ("Tin", 5, "p", "Other Metals"),
    ("Antimony", 5, "p", "Metalloids"),
    ("Tellurium", 5, "p", "Metalloids"),
    ("Iodine", 5, "p", "Halogens"),
    ("Xenon", 5, "p", "Noble Gases"),
    ("Cesium", 6, "s", "Alkali Metals"),
    ("Barium", 6, "s", "Alkaline Earth Metals"),
    ("Lanthanum", 6, "f", "Lanthanides"),
    ("Cerium", 6, "f", "Lanthanides"),
    ("Praseodymium", 6, "f", "Lanthanides"),
    ("Neodymium", 6, "f", "Lanthanides"),
    ("Promethium", 6, "f", "Lanthanides"),
    ("Samarium", 6, "f", "Lanthanides"),
    ("Europium", 6, "f", "Lanthanides"),
    ("Gadolinium", 6, "f", "Lanthanides"),
    ("Terbium", 6, "f", "Lanthanides"),
    ("Dysprosium", 6, "f", "Lanthanides"),
    ("Holmium", 6, "f", "Lanthanides"),
    ("Erbium", 6, "f", "Lanthanides"),
    ("Thulium", 6, "f", "Lanthanides"),
    ("Ytterbium", 6, "f", "Lanthanides"),
    ("Lutetium", 6, "f", "Lanthanides"),
    ("Hafnium", 6, "d", "Other Metals"),
    ("Tantalum", 6, "d", "Other Metals"),
    ("Tungsten", 6, "d", "Other Metals"),
    ("Rhenium", 6, "d", "Other Metals"),
    ("Osmium", 6, "d", "Other Metals"),
    ("Iridium", 6, "d", "Other Metals"),
    ("Platinum", 6, "d", "Other Metals"),
    ("Gold", 6, "d", "Other Metals"),
    ("Mercury", 6, "d", "Other Metals"),
    ("Thallium", 6, "p", "Other Metals"),
    ("Lead", 6, "p", "Other Metals"),
    ("Bismuth", 6, "p", "Other Metals"),
    ("Polonium", 6, "p", "Other Metals"),
    ("Astatine", 6, "p", "Halogens"),
    ("Radon", 6, "p", "Noble Gases"),
    ("Francium", 7, "s", "Alkali Metals"),
    ("Radium", 7, "s", "Alkaline Earth Metals"),
    ("Actinium", 7, "f", "Actinides"),
    ("Thorium", 7, "f", "Actinides"),
    ("Protactinium", 7, "f", "Actinides"),
    ("Uranium", 7, "f", "Actinides"),
    ("Neptunium", 7, "f", "Actinides"),
    ("Plutonium", 7, "f", "Actinides"),
    ("Americium", 7, "f", "Actinides"),
    ("Curium", 7, "f", "Actinides"),
    ("Berkelium", 7, "f", "Actinides"),
    ("Californium", 7, "f", "Actinides"),
    ("Einsteinium", 7, "f", "Actinides"),
    ("Fermium", 7, "f", "Actinides"),
    ("Mendelevium", 7, "f", "Actinides"),
    ("Nobelium", 7, "f", "Actinides"),
    ("Lawrencium", 7, "f", "Actinides"),
    ("Rutherfordium", 7, "d", "Other Metals"),
    ("Dubnium", 7, "d", "Other Metals"),
    ("Seaborgium", 7, "d", "Other Metals"),
    ("Bohrium", 7, "d", "Other Metals"),
    ("Hassium", 7, "d", "Other Metals"),
    ("Meitnerium", 7, "d", "Other Metals"),
    ("Darmstadtium", 7, "d", "Other Metals"),
    ("Roentgenium", 7, "d", "Other Metals"),
    ("Copernicium", 7, "d", "Other Metals"),
    ("Nihonium", 7, "p", "Other Metals"),
    ("Flerovium", 7, "p", "Other Metals"),
    ("Moscovium", 7, "p", "Other Metals"),
    ("Livermorium", 7, "p", "Other Metals"),
    ("Tennessine", 7, "p", "Halogens"),
    ("Oganesson", 7, "p", "Noble Gases"),
]


# Pass 4: chemistry, elements, and stable scientific classifications.
PASS_4_CHEMISTRY_AND_ELEMENTS = _build_element_catalog(PERIODIC_TABLE_ELEMENTS)


# Pass 5: astronomy, planetary science, and earth science sequences.
PASS_5_ASTRONOMY_AND_EARTH_SCIENCE = {
    "Solar System Planets": _entry(
        ["Mercury", "Venus", "Earth", "Mars", "Jupiter", "Saturn", "Uranus", "Neptune"],
        aliases=("planets", "eight planets"),
    ),
    "Recognized Dwarf Planets": _entry(
        ["Ceres", "Pluto", "Haumea", "Makemake", "Eris"],
        aliases=("dwarf planets",),
    ),
    "Inner Planets": _entry(["Mercury", "Venus", "Earth", "Mars"]),
    "Outer Planets": _entry(["Jupiter", "Saturn", "Uranus", "Neptune"]),
    "Terrestrial Planets": _entry(["Mercury", "Venus", "Earth", "Mars"]),
    "Gas Giants": _entry(["Jupiter", "Saturn"]),
    "Ice Giants": _entry(["Uranus", "Neptune"]),
    "Galilean Moons": _entry(["Io", "Europa", "Ganymede", "Callisto"]),
    "Zodiac Constellations": _entry(
        [
            "Aries",
            "Taurus",
            "Gemini",
            "Cancer",
            "Leo",
            "Virgo",
            "Libra",
            "Scorpius",
            "Sagittarius",
            "Capricornus",
            "Aquarius",
            "Pisces",
        ]
    ),
    "Western Zodiac Signs": _entry(
        [
            "Aries",
            "Taurus",
            "Gemini",
            "Cancer",
            "Leo",
            "Virgo",
            "Libra",
            "Scorpio",
            "Sagittarius",
            "Capricorn",
            "Aquarius",
            "Pisces",
        ],
        aliases=("zodiac signs", "astrological signs"),
    ),
    "Chinese Zodiac Animals": _entry(
        [
            "Rat",
            "Ox",
            "Tiger",
            "Rabbit",
            "Dragon",
            "Snake",
            "Horse",
            "Goat",
            "Monkey",
            "Rooster",
            "Dog",
            "Pig",
        ],
        aliases=("chinese zodiac", "shengxiao"),
    ),
    "Eight Moon Phases": _entry(
        [
            "New Moon",
            "Waxing Crescent",
            "First Quarter",
            "Waxing Gibbous",
            "Full Moon",
            "Waning Gibbous",
            "Third Quarter",
            "Waning Crescent",
        ],
        aliases=("moon phase cycle",),
    ),
    "Layers of the Atmosphere": _entry(
        ["Troposphere", "Stratosphere", "Mesosphere", "Thermosphere", "Exosphere"]
    ),
    "Geological Eons": _entry(["Hadean", "Archean", "Proterozoic", "Phanerozoic"]),
    "Phanerozoic Eras": _entry(["Paleozoic", "Mesozoic", "Cenozoic"]),
    "Mesozoic Periods": _entry(["Triassic", "Jurassic", "Cretaceous"]),
}


# Pass 6: biology, anatomy, and nutrition taxonomies.
PASS_6_BIOLOGY_AND_HEALTH = {
    "Standard Amino Acids": _entry(
        [
            "Alanine",
            "Arginine",
            "Asparagine",
            "Aspartic acid",
            "Cysteine",
            "Glutamic acid",
            "Glutamine",
            "Glycine",
            "Histidine",
            "Isoleucine",
            "Leucine",
            "Lysine",
            "Methionine",
            "Phenylalanine",
            "Proline",
            "Serine",
            "Threonine",
            "Tryptophan",
            "Tyrosine",
            "Valine",
        ],
        aliases=("amino acids",),
    ),
    "Essential Amino Acids": _entry(
        [
            "Histidine",
            "Isoleucine",
            "Leucine",
            "Lysine",
            "Methionine",
            "Phenylalanine",
            "Threonine",
            "Tryptophan",
            "Valine",
        ]
    ),
    "Branched-Chain Amino Acids": _entry(["Isoleucine", "Leucine", "Valine"]),
    "Aromatic Amino Acids": _entry(["Phenylalanine", "Tyrosine", "Tryptophan"]),
    "Sulfur-Containing Amino Acids": _entry(["Cysteine", "Methionine"]),
    "Positively Charged Amino Acids": _entry(["Arginine", "Histidine", "Lysine"]),
    "Negatively Charged Amino Acids": _entry(["Aspartic acid", "Glutamic acid"]),
    "Polar Uncharged Amino Acids": _entry(
        ["Asparagine", "Cysteine", "Glutamine", "Serine", "Threonine", "Tyrosine"]
    ),
    "Nonpolar Amino Acids": _entry(
        [
            "Alanine",
            "Glycine",
            "Isoleucine",
            "Leucine",
            "Methionine",
            "Phenylalanine",
            "Proline",
            "Tryptophan",
            "Valine",
        ]
    ),
    "DNA Bases": _entry(["Adenine", "Cytosine", "Guanine", "Thymine"]),
    "RNA Bases": _entry(["Adenine", "Cytosine", "Guanine", "Uracil"]),
    "Nucleotide Bases": _entry(["Adenine", "Cytosine", "Guanine", "Thymine", "Uracil"]),
    "Purines": _entry(["Adenine", "Guanine"]),
    "Pyrimidines": _entry(["Cytosine", "Thymine", "Uracil"]),
    "ABO Blood Types": _entry(["A", "B", "AB", "O"]),
    "Rh Factors": _entry(["Positive", "Negative"]),
    "Human Blood Types": _entry(["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]),
    "Human Organ Systems": _entry(
        [
            "Integumentary",
            "Skeletal",
            "Muscular",
            "Nervous",
            "Endocrine",
            "Cardiovascular",
            "Lymphatic",
            "Respiratory",
            "Digestive",
            "Urinary",
            "Reproductive",
        ]
    ),
    "Cranial Nerves": _entry(
        [
            "Olfactory",
            "Optic",
            "Oculomotor",
            "Trochlear",
            "Trigeminal",
            "Abducens",
            "Facial",
            "Vestibulocochlear",
            "Glossopharyngeal",
            "Vagus",
            "Accessory",
            "Hypoglossal",
        ]
    ),
    "Domains of Life": _entry(["Archaea", "Bacteria", "Eukarya"]),
    "Kingdoms of Life": _entry(
        ["Archaebacteria", "Eubacteria", "Protista", "Fungi", "Plantae", "Animalia"]
    ),
    "Vertebrate Classes": _entry(["Fish", "Amphibians", "Reptiles", "Birds", "Mammals"]),
    "Essential Vitamins": _entry(
        [
            "Vitamin A",
            "Vitamin C",
            "Vitamin D",
            "Vitamin E",
            "Vitamin K",
            "Vitamin B1",
            "Vitamin B2",
            "Vitamin B3",
            "Vitamin B5",
            "Vitamin B6",
            "Vitamin B7",
            "Vitamin B9",
            "Vitamin B12",
        ]
    ),
    "Fat-Soluble Vitamins": _entry(["Vitamin A", "Vitamin D", "Vitamin E", "Vitamin K"]),
    "Water-Soluble Vitamins": _entry(
        [
            "Vitamin C",
            "Vitamin B1",
            "Vitamin B2",
            "Vitamin B3",
            "Vitamin B5",
            "Vitamin B6",
            "Vitamin B7",
            "Vitamin B9",
            "Vitamin B12",
        ]
    ),
    "Major Endocrine Glands": _entry(
        [
            "Hypothalamus",
            "Pituitary",
            "Pineal",
            "Thyroid",
            "Parathyroids",
            "Thymus",
            "Adrenal Glands",
            "Pancreas",
            "Gonads",
        ]
    ),
}


NBA_TEAMS = {
    "Eastern Conference": {
        "Atlantic Division": [
            "Boston Celtics",
            "Brooklyn Nets",
            "New York Knicks",
            "Philadelphia 76ers",
            "Toronto Raptors",
        ],
        "Central Division": [
            "Chicago Bulls",
            "Cleveland Cavaliers",
            "Detroit Pistons",
            "Indiana Pacers",
            "Milwaukee Bucks",
        ],
        "Southeast Division": [
            "Atlanta Hawks",
            "Charlotte Hornets",
            "Miami Heat",
            "Orlando Magic",
            "Washington Wizards",
        ],
    },
    "Western Conference": {
        "Northwest Division": [
            "Denver Nuggets",
            "Minnesota Timberwolves",
            "Oklahoma City Thunder",
            "Portland Trail Blazers",
            "Utah Jazz",
        ],
        "Pacific Division": [
            "Golden State Warriors",
            "LA Clippers",
            "Los Angeles Lakers",
            "Phoenix Suns",
            "Sacramento Kings",
        ],
        "Southwest Division": [
            "Dallas Mavericks",
            "Houston Rockets",
            "Memphis Grizzlies",
            "New Orleans Pelicans",
            "San Antonio Spurs",
        ],
    },
}

NFL_TEAMS = {
    "AFC": {
        "East Division": ["Buffalo Bills", "Miami Dolphins", "New England Patriots", "New York Jets"],
        "North Division": ["Baltimore Ravens", "Cincinnati Bengals", "Cleveland Browns", "Pittsburgh Steelers"],
        "South Division": ["Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Tennessee Titans"],
        "West Division": ["Denver Broncos", "Kansas City Chiefs", "Las Vegas Raiders", "Los Angeles Chargers"],
    },
    "NFC": {
        "East Division": ["Dallas Cowboys", "New York Giants", "Philadelphia Eagles", "Washington Commanders"],
        "North Division": ["Chicago Bears", "Detroit Lions", "Green Bay Packers", "Minnesota Vikings"],
        "South Division": ["Atlanta Falcons", "Carolina Panthers", "New Orleans Saints", "Tampa Bay Buccaneers"],
        "West Division": ["Arizona Cardinals", "Los Angeles Rams", "San Francisco 49ers", "Seattle Seahawks"],
    },
}

MLB_TEAMS = {
    "American League": {
        "East Division": [
            "Baltimore Orioles",
            "Boston Red Sox",
            "New York Yankees",
            "Tampa Bay Rays",
            "Toronto Blue Jays",
        ],
        "Central Division": [
            "Chicago White Sox",
            "Cleveland Guardians",
            "Detroit Tigers",
            "Kansas City Royals",
            "Minnesota Twins",
        ],
        "West Division": [
            "Houston Astros",
            "Los Angeles Angels",
            "Athletics",
            "Seattle Mariners",
            "Texas Rangers",
        ],
    },
    "National League": {
        "East Division": [
            "Atlanta Braves",
            "Miami Marlins",
            "New York Mets",
            "Philadelphia Phillies",
            "Washington Nationals",
        ],
        "Central Division": [
            "Chicago Cubs",
            "Cincinnati Reds",
            "Milwaukee Brewers",
            "Pittsburgh Pirates",
            "St. Louis Cardinals",
        ],
        "West Division": [
            "Arizona Diamondbacks",
            "Colorado Rockies",
            "Los Angeles Dodgers",
            "San Diego Padres",
            "San Francisco Giants",
        ],
    },
}

NHL_TEAMS = {
    "Eastern Conference": {
        "Atlantic Division": [
            "Boston Bruins",
            "Buffalo Sabres",
            "Detroit Red Wings",
            "Florida Panthers",
            "Montreal Canadiens",
            "Ottawa Senators",
            "Tampa Bay Lightning",
            "Toronto Maple Leafs",
        ],
        "Metropolitan Division": [
            "Carolina Hurricanes",
            "Columbus Blue Jackets",
            "New Jersey Devils",
            "New York Islanders",
            "New York Rangers",
            "Philadelphia Flyers",
            "Pittsburgh Penguins",
            "Washington Capitals",
        ],
    },
    "Western Conference": {
        "Central Division": [
            "Chicago Blackhawks",
            "Colorado Avalanche",
            "Dallas Stars",
            "Minnesota Wild",
            "Nashville Predators",
            "St. Louis Blues",
            "Utah Mammoth",
            "Winnipeg Jets",
        ],
        "Pacific Division": [
            "Anaheim Ducks",
            "Calgary Flames",
            "Edmonton Oilers",
            "Los Angeles Kings",
            "Seattle Kraken",
            "San Jose Sharks",
            "Vancouver Canucks",
            "Vegas Golden Knights",
        ],
    },
}


# Pass 7: major North American team leagues and conference/division rollups.
PASS_7_SPORTS_AND_LEAGUES: dict[str, dict[str, Any]] = {}
PASS_7_SPORTS_AND_LEAGUES.update(
    _hierarchical_team_sets(
        "NBA",
        NBA_TEAMS,
        aliases=("nba", "nba teams", "national basketball association teams"),
    )
)
PASS_7_SPORTS_AND_LEAGUES.update(
    _hierarchical_team_sets(
        "NFL",
        NFL_TEAMS,
        aliases=("nfl", "nfl teams", "national football league teams"),
    )
)
PASS_7_SPORTS_AND_LEAGUES.update(
    _hierarchical_team_sets(
        "MLB",
        MLB_TEAMS,
        aliases=("mlb", "mlb teams", "major league baseball teams"),
    )
)
PASS_7_SPORTS_AND_LEAGUES.update(
    _hierarchical_team_sets(
        "NHL",
        NHL_TEAMS,
        aliases=("nhl", "nhl teams", "national hockey league teams"),
    )
)
PASS_7_SPORTS_AND_LEAGUES["Original Six NHL Teams"] = _entry(
    [
        "Boston Bruins",
        "Chicago Blackhawks",
        "Detroit Red Wings",
        "Montreal Canadiens",
        "New York Rangers",
        "Toronto Maple Leafs",
    ]
)


# Pass 8: writing systems, code words, and symbolic alphabets.
PASS_8_WRITING_SYSTEMS_AND_CODES = {
    "NATO Phonetic Alphabet": _entry(
        [
            "Alpha",
            "Bravo",
            "Charlie",
            "Delta",
            "Echo",
            "Foxtrot",
            "Golf",
            "Hotel",
            "India",
            "Juliett",
            "Kilo",
            "Lima",
            "Mike",
            "November",
            "Oscar",
            "Papa",
            "Quebec",
            "Romeo",
            "Sierra",
            "Tango",
            "Uniform",
            "Victor",
            "Whiskey",
            "X-ray",
            "Yankee",
            "Zulu",
        ],
        aliases=("military alphabet", "icao phonetic alphabet"),
    ),
    "Greek Alphabet": _entry(
        [
            "Alpha",
            "Beta",
            "Gamma",
            "Delta",
            "Epsilon",
            "Zeta",
            "Eta",
            "Theta",
            "Iota",
            "Kappa",
            "Lambda",
            "Mu",
            "Nu",
            "Xi",
            "Omicron",
            "Pi",
            "Rho",
            "Sigma",
            "Tau",
            "Upsilon",
            "Phi",
            "Chi",
            "Psi",
            "Omega",
        ]
    ),
    "Greek Vowel Letters": _entry(
        ["Alpha", "Epsilon", "Eta", "Iota", "Omicron", "Upsilon", "Omega"]
    ),
    "Greek Consonant Letters": _entry(
        [
            "Beta",
            "Gamma",
            "Delta",
            "Zeta",
            "Theta",
            "Kappa",
            "Lambda",
            "Mu",
            "Nu",
            "Xi",
            "Pi",
            "Rho",
            "Sigma",
            "Tau",
            "Phi",
            "Chi",
            "Psi",
        ]
    ),
    "Hebrew Alphabet": _entry(
        [
            "Aleph",
            "Bet",
            "Gimel",
            "Dalet",
            "He",
            "Vav",
            "Zayin",
            "Het",
            "Tet",
            "Yod",
            "Kaf",
            "Lamed",
            "Mem",
            "Nun",
            "Samekh",
            "Ayin",
            "Pe",
            "Tsadi",
            "Qof",
            "Resh",
            "Shin",
            "Tav",
        ],
        aliases=("aleph bet", "hebrew letters"),
    ),
    "Hebrew Final Letters": _entry(["Kaf", "Mem", "Nun", "Pe", "Tsadi"]),
    "Roman Numeral Symbols": _entry(["I", "V", "X", "L", "C", "D", "M"]),
    "Morse Code Letters": _entry(list("ABCDEFGHIJKLMNOPQRSTUVWXYZ")),
    "Morse Code Digits": _entry(list("0123456789")),
}


# Pass 9: game systems, card decks, and classical cultural sets.
PASS_9_GAMES_CARDS_AND_MYTH = {
    "Greek Muses": _entry(
        [
            "Calliope",
            "Clio",
            "Erato",
            "Euterpe",
            "Melpomene",
            "Polyhymnia",
            "Terpsichore",
            "Thalia",
            "Urania",
        ]
    ),
    "Tarot Major Arcana Cards": _entry(
        [
            "The Fool",
            "The Magician",
            "The High Priestess",
            "The Empress",
            "The Emperor",
            "The Hierophant",
            "The Lovers",
            "The Chariot",
            "Strength",
            "The Hermit",
            "Wheel of Fortune",
            "Justice",
            "The Hanged Man",
            "Death",
            "Temperance",
            "The Devil",
            "The Tower",
            "The Star",
            "The Moon",
            "The Sun",
            "Judgement",
            "The World",
        ],
        aliases=("major arcana",),
    ),
    "Tarot Minor Arcana Suits": _entry(["Wands", "Cups", "Swords", "Pentacles"]),
    "Tarot Court Cards": _entry(["Page", "Knight", "Queen", "King"]),
    "Tarot Pip Ranks": _entry(
        ["Ace", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"]
    ),
    "Tarot Cups Cards": _entry(_tarot_suit_cards("Cups")),
    "Tarot Pentacles Cards": _entry(_tarot_suit_cards("Pentacles")),
    "Tarot Swords Cards": _entry(_tarot_suit_cards("Swords")),
    "Tarot Wands Cards": _entry(_tarot_suit_cards("Wands")),
    "Playing Card Suits": _entry(["Clubs", "Diamonds", "Hearts", "Spades"]),
    "Playing Card Ranks": _entry(
        ["Ace", "2", "3", "4", "5", "6", "7", "8", "9", "10", "Jack", "Queen", "King"]
    ),
    "Playing Card Face Cards": _entry(["Jack", "Queen", "King"]),
    "Chess Files": _entry(list("ABCDEFGH")),
    "Chess Ranks": _entry([str(i) for i in range(1, 9)]),
    "Chess Piece Types": _entry(["King", "Queen", "Rook", "Bishop", "Knight", "Pawn"]),
    "Classic Clue Suspects": _entry(
        [
            "Miss Scarlet",
            "Colonel Mustard",
            "Mrs. White",
            "Mr. Green",
            "Mrs. Peacock",
            "Professor Plum",
        ]
    ),
    "Classic Clue Weapons": _entry(
        ["Candlestick", "Dagger", "Lead Pipe", "Revolver", "Rope", "Wrench"]
    ),
    "Classic Clue Rooms": _entry(
        [
            "Kitchen",
            "Ballroom",
            "Conservatory",
            "Dining Room",
            "Billiard Room",
            "Library",
            "Lounge",
            "Hall",
            "Study",
        ]
    ),
}


# Pass 10: calendar systems, music theory, and bounded color vocabularies.
PASS_10_CALENDAR_COLOR_AND_MUSIC = {
    "Gregorian Months": _entry(
        [
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ]
    ),
    "Calendar Quarters": _entry(["Q1", "Q2", "Q3", "Q4"]),
    "ISO Weekdays": _entry(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    ),
    "Chromatic Scale Notes": _entry(
        ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    ),
    "Diatonic Scale Notes": _entry(["C", "D", "E", "F", "G", "A", "B"]),
    "Solfege Syllables": _entry(["Do", "Re", "Mi", "Fa", "Sol", "La", "Ti"]),
    "Church Modes": _entry(
        ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
    ),
    "Visible Spectrum Colors": _entry(
        ["Red", "Orange", "Yellow", "Green", "Blue", "Indigo", "Violet"]
    ),
    "Additive Primary Colors": _entry(["Red", "Green", "Blue"]),
    "Subtractive Primary Colors": _entry(["Cyan", "Magenta", "Yellow"]),
    "CMYK Process Colors": _entry(["Cyan", "Magenta", "Yellow", "Key"]),
    "RYB Primary Colors": _entry(["Red", "Yellow", "Blue"]),
    "RYB Secondary Colors": _entry(["Orange", "Green", "Violet"]),
    "Color Wheel Tertiary Colors": _entry(
        [
            "Red-Orange",
            "Yellow-Orange",
            "Yellow-Green",
            "Blue-Green",
            "Blue-Violet",
            "Red-Violet",
        ]
    ),
    "Olympic Ring Colors": _entry(["Blue", "Yellow", "Black", "Green", "Red"]),
}


BUILTIN_CANONICAL_SETS = _merge_passes(
    PASS_1_PERSONALITY_FRAMEWORKS,
    PASS_2_FANTASY_AND_FANDOM,
    PASS_3_GEOGRAPHY_AND_CIVICS,
    PASS_4_CHEMISTRY_AND_ELEMENTS,
    PASS_5_ASTRONOMY_AND_EARTH_SCIENCE,
    PASS_6_BIOLOGY_AND_HEALTH,
    PASS_7_SPORTS_AND_LEAGUES,
    PASS_8_WRITING_SYSTEMS_AND_CODES,
    PASS_9_GAMES_CARDS_AND_MYTH,
    PASS_10_CALENDAR_COLOR_AND_MUSIC,
)
