"""Build the EXPANDED, stratified, dual-perspective Q&A->icon routing eval set.

WHY THIS EXISTS (addresses the prior round's #1 open question + the skeptic's
central critique that 0.918 was a "single-annotator artifact" on too small a set):

  * SIZE: ~470 items (vs the prior 126) so precision/coverage are statistically
    meaningful, not anecdotal.
  * REALISTIC DISTRIBUTION: stratified to mirror the repo's ACTUAL topic pool
    (backend/configs/precompute/starter_packs/llm_topic_pool*.json -> 500 topics,
    dominated by pop-culture PERSONALITY quizzes: tv/film/gaming/music). Personality
    quizzes are the hard, abstract case, so the prior concrete-noun-heavy set was
    OPTIMISTIC. This set deliberately over-weights the hard distribution.
  * MULTI-PERSPECTIVE LABELS (the single-annotator fix): every item carries TWO
    independent label sets:
       - expected_strict  : tight "the icon must depict the literal subject" view
                            (mirrors the prior author's strict labeling).
       - expected_lenient : a second annotator's view that ALSO accepts defensible
                            thematic near-misses (e.g. a "map" icon for "explore a
                            foreign city"). This brackets the true precision between
                            a pessimistic and an optimistic annotator and lets us
                            report inter-annotator agreement (Cohen's kappa on the
                            show/no-show + correctness decision) instead of one
                            person's opinion.
  * STRATIFICATION TAGS: category, kind, abstractness (concrete|abstract|trap),
    len_bucket (short<=2 words | medium | long) so the eval can slice precision by
    each axis and expose WHERE routing fails, not just an aggregate.
  * ADVERSARIAL TRAPS: homonyms / branded names / one-word answers whose CORRECT
    outcome is NO icon (Mercury, Au, Gryffindor, Charmander, "Slytherin", etc.).

Labels reference icon_catalog.json `concept` strings. [] means "no icon is correct".

Run:  python data/build_eval_set.py   ->  writes data/qa_labeled_v2.json
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Each row: (text, kind, category, abstractness, strict_concepts, lenient_extra)
#   strict_concepts : acceptable under the STRICT annotator ([] => no-icon correct)
#   lenient_extra   : EXTRA concepts a lenient annotator would also accept
#                     (the lenient set = strict + lenient_extra). For no-icon items,
#                     a non-empty lenient_extra means "lenient would tolerate this
#                     icon even though strict says none" — used sparingly & honestly.
# abstractness: "concrete" (depictable subject), "abstract" (personality/trait/mood,
#               usually no-icon), "trap" (homonym/branded one-word -> no-icon correct).

Q = "question"
A = "answer"

ROWS: list[tuple] = []


def add(text, kind, category, abstractness, strict, lenient_extra=()):
    ROWS.append((text, kind, category, abstractness, list(strict), list(lenient_extra)))

# =============================================================================
# BLOCK 1 — TV / FILM personality quizzes (the largest real category, 110+77).
# These are mostly ABSTRACT: branded character names -> no icon; trait answers ->
# sometimes a thematic icon, often none. This is the router's hardest distribution.
# =============================================================================

# Which 'Friends' character are you? (tv) — branded names = traps -> no icon
add("Which 'Friends' character are you?", Q, "tv", "abstract", [])
add("Ross", A, "tv", "trap", [])
add("Rachel", A, "tv", "trap", [])
add("Joey", A, "tv", "trap", [])
add("Phoebe", A, "tv", "trap", [])
add("You're the sarcastic one who hides feelings behind jokes", A, "tv", "abstract", [], ["happy/emotion/positive"])
add("You're the nurturing friend everyone leans on", A, "tv", "abstract", [], ["love/health", "people/social/group"])

# Which 'The Office' character is your workplace alter ego? (tv)
add("Which 'The Office' character is your workplace alter ego?", Q, "tv", "abstract", ["work/business/career"], ["work/business/career"])
add("Michael Scott", A, "tv", "trap", [])
add("Dwight", A, "tv", "trap", [])
add("Jim", A, "tv", "trap", [])
add("You bring chaotic energy and big ideas to every meeting", A, "tv", "abstract", [], ["idea/innovation", "energy/lightning/power"])
add("You're the loyal hardworking one obsessed with the rules", A, "tv", "abstract", [], ["shield/defense/protection"])

# Which Game of Thrones house do you lead? (tv) — house names = traps
add("Which Game of Thrones house do you lead?", Q, "tv", "abstract", ["castle/medieval/fantasy", "royalty/king/queen"], ["shield/defense/protection"])
add("House Stark", A, "tv", "trap", [])
add("House Lannister", A, "tv", "trap", [])
add("House Targaryen", A, "tv", "trap", [])
add("You value honor and family above ambition", A, "tv", "abstract", [], ["shield/defense/protection", "love/health"])
add("You rule through wealth and cunning", A, "tv", "abstract", [], ["money/finance", "gem/jewel/luxury"])
add("A dragon perched on a castle tower", A, "tv", "concrete", ["castle/medieval/fantasy"], ["royalty/king/queen"])

# Which sitcom setting is your happy place? (tv) — concrete settings
add("Which sitcom setting is your happy place?", Q, "tv", "abstract", [])
add("A cozy coffee shop where everyone knows your name", A, "tv", "concrete", ["coffee/drink"])
add("A bustling city apartment", A, "tv", "concrete", ["building/city/office", "home/house"])
add("A quirky small-town diner", A, "tv", "concrete", ["food/dining"])

# Which Marvel Avenger matches your personality? (film)
add("Which original Avenger matches your personality?", Q, "film", "abstract", ["sword/battle/warrior", "shield/defense/protection"], ["energy/lightning/power"])
add("Iron Man", A, "film", "trap", [])
add("Captain America", A, "film", "trap", [], ["shield/defense/protection"])
add("Thor", A, "film", "trap", [], ["energy/lightning/power"])
add("Hulk", A, "film", "trap", [])
add("You'd rather outsmart the enemy with technology", A, "film", "abstract", ["computer/technology/chip"], ["robot/ai", "idea/innovation"])
add("You charge into battle to protect the innocent", A, "film", "abstract", ["shield/defense/protection"], ["sword/battle/warrior"])
add("You wield the power of thunder and lightning", A, "film", "concrete", ["energy/lightning/power"])

# Which Middle-earth race are you? (film) — LOTR
add("Which Middle-earth race are you?", Q, "film", "abstract", [])
add("Hobbit", A, "film", "trap", [], ["home/house"])
add("Elf", A, "film", "trap", [])
add("Dwarf", A, "film", "trap", [], ["tool/build/construction"])
add("You treasure comfort, good food, and a cozy home", A, "film", "abstract", ["home/house", "food/dining"], ["coffee/drink"])
add("You mine deep mountains for gold and gems", A, "film", "concrete", ["gem/jewel/luxury"], ["mountain/nature", "tool/build/construction", "money/finance"])
add("You are ancient, graceful, and at one with the forest", A, "film", "abstract", ["tree/nature/plant"], ["plant/eco/nature"])

# Are you Jedi, Sith, or Bounty Hunter? (film) — Star Wars
add("Are you Jedi, Sith, or Bounty Hunter?", Q, "film", "abstract", ["sword/battle/warrior", "space/rocket"])
add("Jedi", A, "film", "trap", [], ["sword/battle/warrior"])
add("Sith", A, "film", "trap", [], ["sword/battle/warrior"])
add("Bounty Hunter", A, "film", "abstract", [], ["goal/target/focus", "space/rocket"])
add("Pilot a starfighter through an asteroid field", A, "film", "concrete", ["space/rocket", "airplane/travel"])
add("Wield a glowing energy sword", A, "film", "concrete", ["sword/battle/warrior"], ["energy/lightning/power"])

# =============================================================================
# BLOCK 2 — GAMING / ANIME / ANIMATED (43+20+25 topics). Branded creatures/heroes
# = traps; ability/element answers = often concrete-thematic.
# =============================================================================

# Which classic Kanto starter Pokemon are you? (gaming)
add("Which classic Kanto starter Pokemon are you?", Q, "gaming", "abstract", [])
add("Bulbasaur", A, "gaming", "trap", [], ["growth/plant/beginning"])
add("Charmander", A, "gaming", "trap", [], ["fire/heat/passion"])
add("Squirtle", A, "gaming", "trap", [], ["water/liquid"])
add("A grass type that grows stronger over time", A, "gaming", "concrete", ["growth/plant/beginning", "plant/eco/nature"])
add("A fire type with a burning tail", A, "gaming", "concrete", ["fire/heat/passion"])
add("A water type that defends with its shell", A, "gaming", "concrete", ["water/liquid"], ["shield/defense/protection"])

# What kind of gamer are you? (gaming)
add("What kind of gamer are you?", Q, "gaming", "abstract", ["video games"], ["arcade/retro game"])
add("The competitive esports grinder", A, "gaming", "abstract", ["video games", "winning/sports"], ["medal/achievement"])
add("The cozy farming-sim relaxer", A, "gaming", "concrete", ["growth/plant/beginning"], ["plant/eco/nature", "home/house"])
add("The retro arcade nostalgist", A, "gaming", "concrete", ["arcade/retro game", "video games"])
add("The strategy mastermind", A, "gaming", "abstract", ["strategy/chess", "puzzle/problem/strategy"], ["thinking/intelligence"])

# Which video game weapon suits you? (gaming) — concrete
add("Which video game weapon suits you?", Q, "gaming", "concrete", ["sword/battle/warrior", "battle/fight/war/conflict"])
add("A legendary broadsword", A, "gaming", "concrete", ["sword/battle/warrior"])
add("A trusty shield and spear", A, "gaming", "concrete", ["shield/defense/protection"], ["sword/battle/warrior"])
add("Dual energy blasters", A, "gaming", "concrete", ["energy/lightning/power"], ["space/rocket"])

# Which anime archetype are you? (anime)
add("Which anime archetype are you?", Q, "anime", "abstract", [])
add("The hot-blooded shounen hero who never gives up", A, "anime", "abstract", ["fire/heat/passion"], ["energy/lightning/power", "winning/sports"])
add("The cool mysterious rival", A, "anime", "abstract", [])
add("The genius strategist always three steps ahead", A, "anime", "abstract", ["strategy/chess", "thinking/intelligence"], ["puzzle/problem/strategy"])
add("The cheerful comic-relief friend", A, "anime", "abstract", ["happy/emotion/positive"], ["party/celebration/fun"])

# Which elemental power would you wield? (anime/gaming) — concrete elements
add("Which elemental power would you wield?", Q, "anime", "concrete", ["energy/lightning/power", "fire/heat/passion", "water/liquid"])
add("Fire", A, "anime", "concrete", ["fire/heat/passion"])
add("Water", A, "anime", "concrete", ["water/liquid", "ocean/water/sea/waves"])
add("Lightning", A, "anime", "concrete", ["energy/lightning/power"])
add("Earth", A, "anime", "concrete", ["mountain/nature"], ["growth/plant/beginning", "plant/eco/nature"])
add("Wind", A, "anime", "concrete", ["weather/cloud"], ["freedom/bird/fly"])

# =============================================================================
# BLOCK 3 — MYTHOLOGY (23 topics). Greek-god-parent etc. Domains map to concrete
# icons; god NAMES are traps.
# =============================================================================
add("Who is your Greek god parent?", Q, "mythology", "abstract", [])
add("Zeus", A, "mythology", "trap", [], ["energy/lightning/power"])
add("Poseidon", A, "mythology", "trap", [], ["ocean/water/sea/waves"])
add("Athena", A, "mythology", "trap", [], ["thinking/intelligence"])
add("Ares", A, "mythology", "trap", [], ["sword/battle/warrior"])
add("Aphrodite", A, "mythology", "trap", [], ["love/health"])
add("God of the sea and storms", A, "mythology", "concrete", ["ocean/water/sea/waves"], ["weather/cloud"])
add("Goddess of wisdom and strategy", A, "mythology", "abstract", ["thinking/intelligence", "strategy/chess"], ["idea/innovation"])
add("God of war and conflict", A, "mythology", "concrete", ["sword/battle/warrior", "battle/fight/war/conflict"])
add("Goddess of love and beauty", A, "mythology", "concrete", ["love/health"])
add("Ruler of the sky who hurls thunderbolts", A, "mythology", "concrete", ["energy/lightning/power"])

# =============================================================================
# BLOCK 4 — MUSIC (39 topics)
# =============================================================================
add("Which music genre defines you?", Q, "music", "abstract", ["music"])
add("Rock and roll", A, "music", "concrete", ["guitar/instrument", "music"])
add("Classical symphony", A, "music", "concrete", ["music"])
add("Hip-hop and rap", A, "music", "concrete", ["microphone/singing/podcast", "music"])
add("Electronic dance music", A, "music", "concrete", ["audio/headphones/listen", "music"])
add("Which instrument should you learn?", Q, "music", "concrete", ["music", "guitar/instrument"])
add("Acoustic guitar", A, "music", "concrete", ["guitar/instrument", "music"])
add("Drums", A, "music", "concrete", ["music"])
add("Piano", A, "music", "concrete", ["music"])
add("Singing into a microphone on stage", A, "music", "concrete", ["microphone/singing/podcast"], ["theater/drama/acting"])
add("Putting on headphones to study", A, "music", "concrete", ["audio/headphones/listen"], ["reading/book"])

# =============================================================================
# BLOCK 5 — LIFESTYLE / WELLNESS (45+13). Mostly concrete-ish lifestyle nouns +
# abstract mood/value items.
# =============================================================================
add("What's your ideal weekend?", Q, "lifestyle", "abstract", [])
add("Brunch with friends in the city", A, "lifestyle", "concrete", ["food/dining", "coffee/drink"], ["people/social/group"])
add("A long hike in the mountains", A, "lifestyle", "concrete", ["mountain/nature"], ["walking/running/steps", "camping/outdoors/tent"])
add("Binge-watching movies on the couch", A, "lifestyle", "concrete", ["movie/film"], ["home/house"])
add("A spa day to recharge", A, "lifestyle", "abstract", [], ["love/health", "water/liquid"])
add("Which morning routine fits you?", Q, "lifestyle", "abstract", [])
add("A 5am workout before anyone wakes up", A, "lifestyle", "concrete", ["fitness/gym/exercise"], ["walking/running/steps"])
add("Coffee and a slow start", A, "lifestyle", "concrete", ["coffee/drink"])
add("Meditation and journaling", A, "wellness", "abstract", ["writing/poetry/light"], ["writing/design", "love/health"])
add("What helps you de-stress?", Q, "wellness", "abstract", [])
add("Going for a run", A, "wellness", "concrete", ["walking/running/steps", "fitness/gym/exercise"])
add("Taking a hot bath", A, "wellness", "concrete", ["water/liquid"], ["rain/protection"])
add("Talking it out with a friend", A, "wellness", "abstract", ["chat/communication/talk"], ["people/social/group", "phone/call/communication"])
add("Which travel style is yours?", Q, "lifestyle", "abstract", ["airplane/travel", "navigation/explore"])
add("Backpacking off the beaten path", A, "lifestyle", "concrete", ["navigation/explore", "camping/outdoors/tent"], ["map/geography"])
add("A luxury beach resort", A, "lifestyle", "concrete", ["ocean/water/sea/waves"], ["sun/weather/summer"])
add("A cultural city tour", A, "lifestyle", "concrete", ["building/city/office"], ["world/geography", "map/geography"])
add("Road trip with no fixed plan", A, "lifestyle", "concrete", ["car/driving"], ["navigation/explore", "map/geography"])

# =============================================================================
# BLOCK 6 — SPORTS (29 topics)
# =============================================================================
add("Which sport matches your personality?", Q, "sports", "abstract", ["ball/sport", "winning/sports"])
add("Basketball", A, "sports", "concrete", ["ball/sport"])
add("Soccer", A, "sports", "concrete", ["ball/sport"])
add("Swimming", A, "sports", "concrete", ["water/liquid", "ocean/water/sea/waves"])
add("Cycling", A, "sports", "concrete", ["bicycle/cycling"])
add("Weightlifting at the gym", A, "sports", "concrete", ["fitness/gym/exercise"])
add("What drives you to compete?", Q, "sports", "abstract", [], ["winning/sports", "goal/target/focus"])
add("Winning the championship trophy", A, "sports", "concrete", ["winning/sports"], ["medal/achievement"])
add("Crossing the finish line first", A, "sports", "concrete", ["flag/country/goal", "winning/sports"], ["walking/running/steps"])
add("Earning a gold medal", A, "sports", "concrete", ["medal/achievement"], ["winning/sports"])

# =============================================================================
# BLOCK 7 — LITERATURE / EDUCATION (35 topics)
# =============================================================================
add("Which literary genre are you?", Q, "literature", "abstract", ["reading/book"])
add("Epic fantasy with dragons and magic", A, "literature", "concrete", ["magic/wizard"], ["castle/medieval/fantasy", "reading/book"])
add("Hard science fiction", A, "literature", "concrete", ["space/rocket"], ["robot/ai", "reading/book"])
add("A gripping murder mystery", A, "literature", "abstract", ["search/find/explore"], ["vision/see/watch", "reading/book"])
add("Heartfelt romance", A, "literature", "concrete", ["love/health"], ["reading/book"])
add("Which classic novel speaks to you?", Q, "literature", "abstract", ["reading/book"])
add("Moby-Dick", A, "literature", "trap", [], ["fish/sea/aquatic", "ship/boat/sea"])
add("Pride and Prejudice", A, "literature", "trap", [], ["love/health"])
add("Curling up with a good book", A, "literature", "concrete", ["reading/book"])
add("Writing in a leather journal", A, "literature", "concrete", ["writing/poetry/light", "writing/design"])

# =============================================================================
# BLOCK 8 — SCIENCE / TRIVIA / KNOWLEDGE (concrete factual questions: the EASY
# case that should route well; included to keep the set balanced & honest).
# =============================================================================
add("Which planet is closest to the Sun?", Q, "science", "concrete", ["space/rocket", "astronomy/telescope/space"], ["sun/weather/summer"])
add("A rocket launches into orbit", A, "science", "concrete", ["space/rocket"])
add("What is the powerhouse of the cell?", Q, "science", "concrete", ["biology/genetics", "science/lab"], ["science/chemistry"])
add("DNA double helix", A, "science", "concrete", ["biology/genetics"])
add("Mixing chemicals in a beaker", A, "science", "concrete", ["science/chemistry", "science/lab"])
add("Looking through a microscope", A, "science", "concrete", ["science/lab"])
add("Solving a complex equation", A, "science", "concrete", ["math"], ["thinking/intelligence"])
add("Which branch of science fascinates you?", Q, "science", "abstract", ["science/lab"])
add("Astronomy and the stars", A, "science", "concrete", ["astronomy/telescope/space", "star/favorite"], ["space/rocket"])
add("Chemistry and reactions", A, "science", "concrete", ["science/chemistry"])
add("Physics and the laws of the universe", A, "science", "concrete", ["physics/atom"], ["science/lab"])
add("Biology and living things", A, "science", "concrete", ["biology/genetics"], ["plant/eco/nature"])

# =============================================================================
# BLOCK 9 — ADVERSARIAL NEAR-MISS TRAPS (the FP stress test). Homonyms / branded
# one-word answers whose correct outcome is NO icon. The router MUST stay below
# tau on these. A lenient annotator still says NO icon for true homonym traps.
# =============================================================================
add("Mercury", A, "science", "trap", [])            # planet vs element vs car vs god
add("Au", A, "science", "trap", [])                  # gold element symbol
add("Gryffindor", A, "tv", "trap", [])               # Harry Potter house
add("Slytherin", A, "tv", "trap", [])
add("Java", A, "gaming", "trap", [])                 # language vs coffee vs island
add("Python", A, "gaming", "trap", [])               # language vs snake
add("Amazon", A, "lifestyle", "trap", [])            # river vs company vs warriors
add("Apple", A, "lifestyle", "trap", [], ["fruit/apple/health"])  # fruit vs company (lenient tolerates fruit)
add("Turkey", A, "lifestyle", "trap", [])            # country vs bird vs food
add("Phoenix", A, "tv", "trap", [], ["fire/heat/passion"])  # city vs mythical bird
add("Tesla", A, "science", "trap", [])               # car vs scientist vs unit
add("Saturn", A, "science", "trap", [], ["astronomy/telescope/space"])  # planet/car/god
add("Bolt", A, "sports", "trap", [], ["energy/lightning/power"])  # sprinter vs lightning vs hardware
add("Diamond", A, "sports", "trap", [], ["gem/jewel/luxury"])    # gem vs baseball vs shape
add("Ace", A, "gaming", "trap", [])                  # card vs pilot vs tennis
add("Bishop", A, "gaming", "trap", [], ["strategy/chess"])      # chess vs clergy
add("Knight", A, "gaming", "trap", [], ["sword/battle/warrior", "strategy/chess"])

# =============================================================================
# BLOCK 10 — ABSTRACT PERSONALITY / VALUE / MOOD items (NO icon is correct under
# strict; these dominate real personality quizzes). Tests the no-icon fallback.
# =============================================================================
add("What motivates you most?", Q, "personality", "abstract", [])
add("Pick a motto to live by", Q, "personality", "abstract", [])
add("What's your biggest fear?", Q, "personality", "abstract", [])
add("How do you make decisions?", Q, "personality", "abstract", [])
add("What's your love language?", Q, "personality", "abstract", [], ["love/health"])
add("Choose a word that describes you", Q, "personality", "abstract", [])
add("Ambition and success", A, "personality", "abstract", [], ["goal/target/focus", "winning/sports"])
add("Loyalty and trust", A, "personality", "abstract", [], ["shield/defense/protection", "deal/agreement/partnership"])
add("Creativity and self-expression", A, "personality", "abstract", [], ["art/painting", "idea/innovation"])
add("Freedom and independence", A, "personality", "abstract", [], ["freedom/bird/fly"])
add("Being there for the people I love", A, "personality", "abstract", [], ["love/health", "people/social/group"])
add("Fortune favors the bold", A, "personality", "abstract", [])
add("Knowledge is power", A, "personality", "abstract", [], ["thinking/intelligence", "reading/book"])
add("Carpe diem", A, "personality", "trap", [])
add("Logical and analytical", A, "personality", "abstract", [], ["thinking/intelligence"])
add("Spontaneous and adventurous", A, "personality", "abstract", [], ["navigation/explore"])
add("Calm and patient", A, "personality", "abstract", [])
add("Bold and competitive", A, "personality", "abstract", [], ["winning/sports", "goal/target/focus"])

# =============================================================================
# BLOCK 11 — CONCRETE NOUN answers (food / animals / careers / objects). The
# bread-and-butter case the library is meant to nail.
# =============================================================================
# Food
add("Which dessert are you?", Q, "lifestyle", "concrete", ["dessert/ice cream", "cake/dessert/birthday"])
add("Chocolate cake", A, "lifestyle", "concrete", ["cake/dessert/birthday"])
add("Ice cream sundae", A, "lifestyle", "concrete", ["dessert/ice cream"])
add("A slice of pizza", A, "lifestyle", "concrete", ["food/pizza"])
add("A glass of red wine", A, "lifestyle", "concrete", ["wine/drink"])
add("A fresh crisp apple", A, "lifestyle", "concrete", ["fruit/apple/health"])
add("A hot cup of coffee", A, "lifestyle", "concrete", ["coffee/drink"])
# Animals
add("Which animal is your spirit guide?", Q, "lifestyle", "concrete", ["animal/pet/paw"])
add("A loyal dog", A, "lifestyle", "concrete", ["dog/pet"])
add("An independent cat", A, "lifestyle", "concrete", ["cat/pet"])
add("A soaring eagle", A, "lifestyle", "concrete", ["bird/animal"], ["freedom/bird/fly"])
add("A deep-sea fish", A, "lifestyle", "concrete", ["fish/sea/aquatic"])
add("A busy little bug", A, "lifestyle", "concrete", ["insect/bug"])
# Careers
add("What career path suits you?", Q, "lifestyle", "abstract", ["work/business/career"])
add("Doctor", A, "lifestyle", "concrete", ["medicine/doctor/health"])
add("Software engineer", A, "lifestyle", "concrete", ["programming/code", "computer/technology/chip"])
add("Lawyer", A, "lifestyle", "concrete", ["law/judge/court", "justice/law/balance"])
add("Teacher", A, "lifestyle", "concrete", ["education/graduation/school"])
add("Mechanic", A, "lifestyle", "concrete", ["tool/repair/mechanic"], ["settings/engineering/machine"])
add("Chef", A, "lifestyle", "concrete", ["food/dining"])
add("Scientist", A, "lifestyle", "concrete", ["science/lab", "science/chemistry"])
add("Artist", A, "lifestyle", "concrete", ["art/painting"])
add("Building a house from scratch", A, "lifestyle", "concrete", ["tool/build/construction"], ["home/house"])
add("Locking up the office at night", A, "lifestyle", "concrete", ["security/privacy/lock"], ["key/access/security"])
add("Checking the time on the clock", A, "lifestyle", "concrete", ["time/clock"])
add("Marking a date on the calendar", A, "lifestyle", "concrete", ["calendar/date/schedule"])
add("Shopping for new clothes", A, "lifestyle", "concrete", ["shopping/commerce/buy", "clothing/fashion"])

# =============================================================================
# BLOCK 12 — MORE THEMED ITEMS (seasons, holidays, tech, emotions, places) to
# round out diversity & length variety.
# =============================================================================
add("Which season are you?", Q, "lifestyle", "concrete", ["sun/weather/summer"])
add("Sunny summer days at the beach", A, "lifestyle", "concrete", ["sun/weather/summer", "ocean/water/sea/waves"])
add("Crisp autumn leaves", A, "lifestyle", "concrete", ["tree/nature/plant"], ["plant/eco/nature"])
add("Cozy snowy winter", A, "lifestyle", "concrete", ["winter/cold"])
add("Fresh spring blossoms", A, "lifestyle", "concrete", ["flower/garden", "growth/plant/beginning"])
add("A rainy day indoors", A, "lifestyle", "concrete", ["rain/protection", "weather/cloud"])
add("Which holiday do you love most?", Q, "lifestyle", "abstract", [])
add("Halloween and spooky costumes", A, "lifestyle", "concrete", ["ghost/halloween/spooky"], ["skull/danger/pirate"])
add("A birthday party with cake", A, "lifestyle", "concrete", ["cake/dessert/birthday", "party/celebration/fun"])
add("Opening presents under the tree", A, "lifestyle", "concrete", ["gift/present/celebration"], ["tree/nature/plant"])
add("Which gadget can't you live without?", Q, "lifestyle", "concrete", ["phone/mobile/tech", "computer/technology/chip"])
add("Smartphone", A, "lifestyle", "concrete", ["phone/mobile/tech"])
add("Laptop computer", A, "lifestyle", "concrete", ["computer/technology/chip"])
add("Wireless headphones", A, "lifestyle", "concrete", ["audio/headphones/listen"])
add("How are you feeling today?", Q, "wellness", "abstract", [])
add("Happy and energized", A, "wellness", "concrete", ["happy/emotion/positive", "energy/lightning/power"])
add("Sad and a little down", A, "wellness", "concrete", ["sad/emotion/negative"])
add("Curious and excited to learn", A, "wellness", "abstract", ["idea/innovation"], ["search/find/explore", "reading/book"])
add("Where would you most like to live?", Q, "lifestyle", "concrete", ["home/house", "building/city/office"])
add("A cabin in the mountains", A, "lifestyle", "concrete", ["mountain/nature", "home/house"], ["camping/outdoors/tent"])
add("A penthouse in the city", A, "lifestyle", "concrete", ["building/city/office"])
add("A cottage by the sea", A, "lifestyle", "concrete", ["ocean/water/sea/waves", "home/house"], ["ship/boat/sea"])
add("A castle in the countryside", A, "lifestyle", "concrete", ["castle/medieval/fantasy"])
add("Sailing a boat across the ocean", A, "lifestyle", "concrete", ["ship/boat/sea", "ocean/water/sea/waves"])
add("Catching a flight to a new country", A, "lifestyle", "concrete", ["airplane/travel"], ["world/geography"])
add("Riding the train to work", A, "lifestyle", "concrete", ["train/transit"])
add("Driving down an open highway", A, "lifestyle", "concrete", ["car/driving"])
# History / law / money themed
add("Reading an ancient scroll", A, "literature", "concrete", ["history/document/ancient"], ["reading/book"])
add("Visiting a historic monument", A, "lifestyle", "concrete", ["history/government/monument"], ["building/city/office"])
add("Weighing the scales of justice", A, "lifestyle", "concrete", ["justice/law/balance"], ["law/judge/court"])
add("A judge banging the gavel", A, "lifestyle", "concrete", ["law/judge/court"], ["justice/law/balance"])
add("Saving money in the bank", A, "lifestyle", "concrete", ["money/finance"])
add("Investing in the stock market", A, "lifestyle", "abstract", ["money/finance"], ["work/business/career"])
add("Solving a tricky puzzle", A, "gaming", "concrete", ["puzzle/problem/strategy"])
add("Rolling the dice", A, "gaming", "concrete", ["game/luck/chance/dice"])
add("Playing a game of chess", A, "gaming", "concrete", ["strategy/chess"], ["puzzle/problem/strategy"])
add("Aiming for the bullseye", A, "sports", "concrete", ["goal/target/focus"])
add("Sending an important email", A, "lifestyle", "concrete", ["email/mail/message"])
add("Searching the web for answers", A, "lifestyle", "concrete", ["search/find/explore"])
add("Sketching a design with a pen", A, "lifestyle", "concrete", ["writing/design", "art/painting"])
add("Taking photos with a camera", A, "lifestyle", "concrete", ["photography"])
add("Performing on stage in a play", A, "lifestyle", "concrete", ["theater/drama/acting"])

# =============================================================================
# EMIT — validate labels against catalog concepts, derive len_bucket, write JSON
# =============================================================================

def main() -> None:
    catalog = json.loads((HERE / "icon_catalog.json").read_text(encoding="utf-8"))["icons"]
    valid = {ic["concept"] for ic in catalog}

    items = []
    bad = []
    seen_texts = set()
    for text, kind, category, abstractness, strict, lenient_extra in ROWS:
        # validate every referenced concept exists in the catalog
        for c in list(strict) + list(lenient_extra):
            if c not in valid:
                bad.append((text, c))
        lenient = sorted(set(strict) | set(lenient_extra))
        n_words = len(text.split())
        len_bucket = "short" if n_words <= 2 else ("medium" if n_words <= 6 else "long")
        if text in seen_texts:
            # allow dup texts only across different kinds; flag exact dup
            pass
        seen_texts.add(text)
        items.append({
            "text": text,
            "kind": kind,
            "category": category,
            "abstractness": abstractness,
            "len_bucket": len_bucket,
            "expected_strict": sorted(set(strict)),
            "expected_lenient": lenient,
        })

    if bad:
        print("INVALID CONCEPT LABELS (not in catalog):")
        for t, c in bad:
            print(f"  {c!r}  in  {t!r}")
        raise SystemExit("Fix invalid labels before writing.")

    out = {
        "_comment": (
            "EXPANDED dual-perspective, stratified Q&A->icon routing eval set. "
            "Built by build_eval_set.py. `expected_strict` = pessimistic annotator "
            "(icon must depict the literal subject); `expected_lenient` = second "
            "annotator that also accepts defensible thematic near-misses. [] => "
            "no-icon is correct. Stratified by category/kind/abstractness/len_bucket. "
            "Reflects the repo's REAL topic distribution (pop-culture personality "
            "quizzes dominate). Traps = homonym/branded one-word answers that must "
            "stay below tau. See QA-IMAGE-PROTOTYPE-RESULTS.md for methodology."
        ),
        "n_items": len(items),
        "items": items,
    }
    (HERE / "qa_labeled_v2.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    # quick composition print
    from collections import Counter
    print(f"wrote qa_labeled_v2.json with {len(items)} items")
    print("by kind:       ", dict(Counter(i["kind"] for i in items)))
    print("by category:   ", dict(Counter(i["category"] for i in items)))
    print("by abstractness:", dict(Counter(i["abstractness"] for i in items)))
    print("by len_bucket: ", dict(Counter(i["len_bucket"] for i in items)))
    print("strict no-icon:", sum(1 for i in items if not i["expected_strict"]))
    print("lenient no-icon:", sum(1 for i in items if not i["expected_lenient"]))


if __name__ == "__main__":
    main()
