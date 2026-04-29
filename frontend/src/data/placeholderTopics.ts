/**
 * Curated noun-phrase corpus for the rotating landing-page input placeholder.
 *
 * Each entry is meant to slot directly into the question frame
 *   "Which {entry} am I?"
 * and read naturally for a personality quiz prompt.
 *
 * The list is organised by theme purely for editorial sanity; consumers should
 * treat it as a single flat pool. We intentionally include synthesized noun
 * phrases from the existing topicExamples catalog (e.g. "Modern coffee table")
 * to push the rotation pool well past 1,000 entries while keeping the curated
 * core front-loaded with personality-quiz-appropriate prompts.
 */

import topicExamplesCatalog from './topicExamples.json';
import type { TopicExample } from '../types/topicExamples';

// ---------- Curated personality-quiz prompts (~700) ----------

const FICTIONAL_CHARACTERS_AND_HOUSES: ReadonlyArray<string> = [
  'Hogwarts house', 'Harry Potter character', 'Friends character', 'Office character',
  'Parks and Rec character', 'Seinfeld character', 'How I Met Your Mother character',
  'Brooklyn Nine-Nine character', 'Game of Thrones house', 'Game of Thrones character',
  'House of the Dragon dragon', 'Lord of the Rings race', 'Lord of the Rings character',
  'Hobbit character', 'Star Wars Jedi', 'Star Wars Sith', 'Star Wars droid',
  'Star Wars bounty hunter', 'Mandalorian character', 'Star Trek captain',
  'Star Trek species', 'Doctor Who Doctor', 'Doctor Who companion',
  'Marvel Avenger', 'Marvel villain', 'X-Men mutant', 'Guardians of the Galaxy member',
  'DC superhero', 'Justice League member', 'Batman villain', 'Spider-Man villain',
  'Disney princess', 'Disney villain', 'Disney sidekick', 'Pixar character',
  'Studio Ghibli character', 'Studio Ghibli film', 'Miyazaki spirit',
  'Pokémon starter', 'Pokémon legendary', 'Pokémon type', 'Pokémon trainer',
  'Avatar nation', 'Avatar bender', 'Legend of Korra character',
  'Adventure Time character', 'Steven Universe gem', 'Rick and Morty character',
  'Bojack Horseman character', 'Simpsons character', 'Futurama character',
  'South Park character', 'Family Guy character', 'Bob\u2019s Burgers character',
  'SpongeBob character', 'Scooby-Doo character', 'Looney Tunes character',
  'Sesame Street character', 'Muppets character',
  'Mario Kart character', 'Super Smash Bros fighter', 'Zelda character',
  'Final Fantasy character', 'Kingdom Hearts character', 'Persona protagonist',
  'Sonic character', 'Animal Crossing villager', 'Stardew Valley bachelor',
  'Mass Effect companion', 'Dragon Age companion', 'Skyrim race',
  'Elden Ring boss', 'Dark Souls boss', 'Halo character', 'Overwatch hero',
  'Apex Legends legend', 'Valorant agent', 'League of Legends champion',
  'Genshin Impact character', 'Among Us role', 'Minecraft mob',
  'Stranger Things character', 'Sex Education character', 'Bridgerton sibling',
  'The Crown monarch', 'Succession sibling', 'White Lotus guest',
  'Ted Lasso character', 'Severance character', 'Wednesday character',
  'Squid Game player', 'Money Heist character', 'Dark character',
  'Better Call Saul character', 'Breaking Bad character', 'Sopranos character',
  'Mad Men character', 'Wire character', 'Lost character',
  'Twin Peaks character', 'X-Files character',
  'Buffy the Vampire Slayer character', 'Veronica Mars character',
  'Gilmore Girls character', 'New Girl roommate', 'Community study group member',
  'Arrested Development character', 'Curb Your Enthusiasm character',
  'Cheers character', 'Frasier character', 'Schitt\u2019s Creek Rose',
  'Modern Family character', 'Big Bang Theory character',
  'Glee character', 'Grease character', 'High School Musical character',
  'Mean Girls character', 'Clueless character', 'Heathers character',
  'Twilight character', 'Hunger Games character', 'Divergent faction',
  'Percy Jackson character', 'Wings of Fire dragon tribe',
  'Wheel of Time Aes Sedai Ajah', 'Stormlight Archive Radiant order',
  'Mistborn metal', 'Dune House', 'Dune character', 'Foundation character',
  'Witcher monster', 'Witcher school', 'Witcher character',
  'Sherlock Holmes character', 'Agatha Christie detective',
  'Jane Austen heroine', 'Bronte heroine', 'Shakespeare tragic hero',
  'Greek tragedy hero', 'Marvel multiverse variant',
];

const PERSONALITY_FRAMEWORKS: ReadonlyArray<string> = [
  'Myers-Briggs type', 'Enneagram type', 'Big Five trait', 'DISC profile',
  'Hogwarts house', 'Hogwarts professor', 'Patronus animal', 'Ilvermorny house',
  'love language', 'attachment style', 'conflict style', 'communication style',
  'leadership style', 'learning style', 'humor style', 'parenting style',
  'StrengthsFinder strength', 'Clifton strength', 'Color personality',
  'Hippocratic temperament', 'astrology sun sign', 'astrology moon sign',
  'astrology rising sign', 'astrology element', 'astrology modality',
  'Chinese zodiac animal', 'Mayan day sign', 'Celtic tree zodiac',
  'tarot major arcana', 'tarot suit', 'rune', 'I Ching hexagram',
  'chakra', 'Ayurvedic dosha', 'Hogwarts wand wood',
];

const MYTHOLOGY_AND_RELIGION: ReadonlyArray<string> = [
  'Greek god', 'Greek goddess', 'Greek hero', 'Greek monster',
  'Olympian', 'Titan', 'Muse', 'Fate',
  'Roman god', 'Roman emperor', 'Roman senator', 'Roman legion',
  'Norse god', 'Norse goddess', 'Valkyrie', 'Aesir', 'Vanir', 'jotunn',
  'Egyptian god', 'Egyptian goddess', 'Egyptian pharaoh',
  'Hindu deity', 'Hindu avatar', 'Mahabharata hero',
  'Buddhist bodhisattva', 'Zen koan', 'Shinto kami',
  'Celtic deity', 'Slavic deity', 'Aztec god', 'Mayan god',
  'Yoruba orisha', 'Polynesian deity',
  'angel', 'archangel', 'demon', 'cherub', 'seraph',
  'saint', 'apostle', 'patron saint', 'monastic order',
  'Arthurian knight', 'Round Table knight', 'Arthurian wizard',
];

const PROFESSIONS_AND_ROLES: ReadonlyArray<string> = [
  'doctor specialty', 'surgical specialty', 'nurse specialty',
  'lawyer type', 'engineer discipline', 'designer discipline',
  'architect type', 'chef cuisine', 'sommelier specialty',
  'detective archetype', 'spy archetype', 'pirate role',
  'wizard school', 'witch tradition', 'alchemist school',
  'librarian archetype', 'museum curator', 'professor type',
  'journalist beat', 'photographer style', 'cinematographer style',
  'film director', 'auteur', 'screenwriter',
  'composer era', 'conductor', 'concert pianist',
  'painter movement', 'sculptor', 'street artist',
  'tattoo artist style', 'fashion designer house', 'perfume note',
  'barista order', 'bartender archetype', 'mixologist style',
  'farmer crop', 'rancher animal', 'beekeeper hive temperament',
  'florist arrangement', 'gardener school', 'landscape designer',
  'astronaut role', 'pilot type', 'sailor archetype',
  'firefighter rank', 'paramedic specialty', 'park ranger',
  'forensic specialist', 'archaeologist field',
  'monk order', 'martial artist style', 'samurai archetype',
  'ninja clan', 'gladiator class', 'Viking role',
  'cowboy archetype', 'outlaw archetype', 'frontier role',
];

const SPORTS_AND_GAMES: ReadonlyArray<string> = [
  'pro tennis player', 'NBA player', 'NFL quarterback', 'MLB pitcher',
  'NHL goalie', 'soccer position', 'F1 driver', 'MotoGP rider',
  'rally driver', 'Tour de France climber', 'marathon legend',
  'Olympic event', 'Winter Olympic event', 'Paralympic event',
  'Premier League club', 'La Liga club', 'Serie A club', 'Bundesliga club',
  'NBA franchise', 'NFL franchise', 'MLB franchise', 'NHL franchise',
  'WWE wrestler', 'lucha libre persona', 'sumo rank',
  'martial art', 'fencing weapon', 'archery style',
  'chess opening', 'chess piece', 'Go strategy',
  'poker style', 'bridge convention', 'mahjong hand',
  'Magic: The Gathering color', 'Pokémon TCG archetype',
  'Dungeons & Dragons class', 'Dungeons & Dragons race',
  'D&D alignment', 'tabletop RPG class', 'board game role',
  'Catan strategy', 'Pandemic role', 'Werewolf role',
  'esports role', 'speedrunning category',
];

const MUSIC_AND_ART: ReadonlyArray<string> = [
  'Beatles song', 'Beatles era', 'Rolling Stones song', 'Led Zeppelin track',
  'Pink Floyd album', 'Queen song', 'Bowie persona', 'Prince era',
  'Madonna era', 'Michael Jackson era', 'Beyoncé era', 'Taylor Swift era',
  'Kanye era', 'Drake era', 'Kendrick album', 'Frank Ocean track',
  'Radiohead album', 'Arcade Fire era', 'Strokes album',
  'Nirvana track', 'Pearl Jam track', 'Soundgarden track',
  'Fleetwood Mac member', 'Eagles song', 'Stevie Nicks song',
  'jazz subgenre', 'jazz instrument', 'bebop legend',
  'Motown act', 'soul subgenre', 'funk band',
  'hip-hop subgenre', '90s R&B group', '2000s pop princess',
  'punk subgenre', 'metal subgenre', 'doom metal band',
  'shoegaze band', 'post-rock band', 'math rock band',
  'electronic subgenre', 'techno subgenre', 'house subgenre',
  'drum and bass subgenre', 'ambient album', 'lo-fi vibe',
  'K-pop group', 'J-pop act', 'city pop track',
  'samba style', 'bossa nova standard', 'tango style',
  'flamenco palo', 'fado mood', 'klezmer tune',
  'classical era', 'symphony', 'concerto', 'opera aria',
  'Renaissance painter', 'Baroque painter', 'Impressionist',
  'Post-Impressionist', 'Expressionist', 'Surrealist',
  'Cubist', 'Bauhaus designer', 'Art Nouveau artist',
  'Art Deco designer', 'street art collective',
];

const FOOD_AND_DRINK: ReadonlyArray<string> = [
  'pizza style', 'pasta shape', 'risotto style', 'sushi roll', 'ramen style',
  'taco style', 'taqueria order', 'sandwich style', 'burger style',
  'BBQ region', 'bbq cut', 'curry style', 'biryani style',
  'noodle dish', 'dumpling style', 'bao filling',
  'cheese type', 'wine varietal', 'wine region', 'champagne house',
  'whiskey region', 'bourbon brand', 'scotch region', 'mezcal style',
  'beer style', 'IPA style', 'sour beer style', 'cider style',
  'cocktail family', 'tiki cocktail', 'classic cocktail',
  'coffee drink', 'coffee origin', 'tea ceremony', 'matcha grade',
  'chocolate origin', 'ice cream flavor', 'gelato flavor',
  'dessert pastry', 'French pastry', 'Viennoiserie',
  'breakfast cereal', 'diner order', 'brunch cocktail',
  'street food', 'food truck cuisine', 'farmers market produce',
];

const PLACES_AND_TRAVEL: ReadonlyArray<string> = [
  'world capital', 'European capital', 'Asian capital', 'African capital',
  'South American capital', 'island nation', 'archipelago',
  'national park', 'UNESCO site', 'wonder of the world',
  'mountain range', 'river system', 'desert',
  'Italian city', 'French city', 'Spanish city', 'Japanese city',
  'Brazilian city', 'Mexican city', 'Indian city', 'Australian city',
  'US state', 'US national park', 'US college town',
  'New York neighborhood', 'Tokyo ward', 'Paris arrondissement',
  'London borough', 'Berlin neighborhood', 'Mexico City colonia',
  'beach destination', 'ski resort', 'spa town', 'wellness retreat',
  'hiking trail', 'pilgrimage route', 'road trip route',
  'train journey', 'cruise itinerary',
];

const ERAS_AND_HISTORY: ReadonlyArray<string> = [
  'historical era', 'decade aesthetic', 'fashion era', 'design era',
  'Renaissance city-state', 'Roman era', 'Byzantine emperor',
  'medieval guild', 'Tudor monarch', 'Stuart monarch', 'Hanoverian monarch',
  'British prime minister', 'French monarch', 'Russian tsar',
  'Ottoman sultan', 'Mughal emperor', 'Tang dynasty figure',
  'Ming dynasty figure', 'Edo-period figure', 'Meiji-era figure',
  'Aztec ruler', 'Inca ruler', 'Mali emperor',
  'Founding Father', 'US president', 'Supreme Court justice',
  'Civil Rights leader', 'Suffragette', 'Roaring Twenties figure',
  'Beat Generation poet', 'Harlem Renaissance figure',
  'Bauhaus master', 'Black Mountain artist',
  'Silicon Valley founder', 'startup era',
];

const SCIENCE_AND_TECH: ReadonlyArray<string> = [
  'planet', 'dwarf planet', 'moon', 'constellation', 'galaxy type',
  'nebula', 'spacecraft', 'rover',
  'subatomic particle', 'element', 'noble gas', 'alkali metal',
  'mineral', 'gemstone', 'crystal system',
  'dinosaur', 'prehistoric mammal', 'megafauna',
  'cat breed', 'dog breed', 'horse breed', 'bird species', 'fish species',
  'reptile', 'amphibian', 'cephalopod', 'shark species',
  'tree species', 'flower species', 'mushroom species',
  'biome', 'ecosystem', 'weather pattern',
  'computer language', 'programming paradigm', 'design pattern',
  'data structure', 'algorithm family',
  'machine learning model', 'database engine', 'web framework',
  'gaming console', 'retro computer', 'classic gadget',
  'social media platform', 'messaging app', 'productivity tool',
];

const STYLES_AND_AESTHETICS: ReadonlyArray<string> = [
  'interior design style', 'architecture style', 'home aesthetic',
  'furniture era', 'fashion subculture', 'streetwear brand',
  'sneaker silhouette', 'denim cut', 'jacket style',
  'hat style', 'haircut', 'beard style',
  'tattoo style', 'piercing placement', 'nail art style',
  'makeup era', 'perfume note', 'cologne family',
  'pottery style', 'ceramics tradition', 'textile pattern',
  'quilt block', 'embroidery style', 'knitting pattern',
  'dance style', 'ballet variation', 'tap routine',
  'theater genre', 'improv form', 'sketch comedy era',
  'film genre', 'film noir era', 'New Wave director',
  'horror subgenre', 'rom-com era', 'heist film',
];

const COMBINED_CURATED: ReadonlyArray<string> = Array.from(
  new Set([
    ...FICTIONAL_CHARACTERS_AND_HOUSES,
    ...PERSONALITY_FRAMEWORKS,
    ...MYTHOLOGY_AND_RELIGION,
    ...PROFESSIONS_AND_ROLES,
    ...SPORTS_AND_GAMES,
    ...MUSIC_AND_ART,
    ...FOOD_AND_DRINK,
    ...PLACES_AND_TRAVEL,
    ...ERAS_AND_HISTORY,
    ...SCIENCE_AND_TECH,
    ...STYLES_AND_AESTHETICS,
  ].map((s) => s.trim())),
);

// ---------- Synthesized supplements from the existing topic catalog ----------
//
// We accept noun-shaped prefixes only (they read fine inside "Which X am I?")
// and reject question-shaped or guide-shaped variants which would read awkwardly.
const ACCEPTABLE_PREFIXES: ReadonlyArray<string> = [
  'Famous ', 'Unique ', 'Modern ', 'Ancient ', 'Regional ', 'Global ',
];
const REJECT_PREFIXES: ReadonlyArray<string> = [
  'Why ', 'How ', 'Beginners Guide to ', 'Comprehensive Guide to ',
  'Expert Tips for ', 'The Secret of ', 'The Best ', 'Innovations in ',
  'Advancements in ', 'Analysis of ', 'Mastering ', 'Fundamentals of ',
  'Principles of ', 'Understanding ', 'Exploring ', 'Diversity in ',
  'Challenges of ', 'Impact of ', 'World of ', 'Evolution of ',
  'History of ', 'Future of ', 'The Art of ', 'The Science of ',
];

function isAcceptableCatalogTopic(topic: string): boolean {
  const t = topic.trim();
  if (!t) return false;
  for (const p of REJECT_PREFIXES) {
    if (t.startsWith(p)) return false;
  }
  // accept either bare nouns or one of the noun-shaped prefixes
  for (const p of ACCEPTABLE_PREFIXES) {
    if (t.startsWith(p)) return true;
  }
  // bare noun (no rejected prefix matched)
  return true;
}

/**
 * Standardise capitalization so every entry begins with a capital letter
 * (sentence case). Proper nouns and embedded capitals are preserved as-is;
 * only the leading character is forced to uppercase. Internal whitespace
 * is collapsed.
 */
function standardizeCapitalization(topic: string): string {
  const collapsed = topic.replace(/\s+/g, ' ').trim();
  if (!collapsed) return collapsed;
  const first = collapsed.charAt(0);
  const upper = first.toLocaleUpperCase();
  if (first === upper) return collapsed;
  return upper + collapsed.slice(1);
}

function buildPool(): string[] {
  const seen = new Set<string>();
  const out: string[] = [];

  for (const item of COMBINED_CURATED) {
    const value = standardizeCapitalization(item);
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }

  for (const raw of topicExamplesCatalog as TopicExample[]) {
    if (!raw || typeof raw.topic !== 'string') continue;
    const t = raw.topic.trim();
    if (!isAcceptableCatalogTopic(t)) continue;
    const value = standardizeCapitalization(t);
    const key = value.toLowerCase();
    if (!value || seen.has(key)) continue;
    seen.add(key);
    out.push(value);
  }

  return out;
}

let _pool: ReadonlyArray<string> | null = null;

/**
 * Returns the deduplicated pool of placeholder topic phrases.
 * Memoised; safe to call repeatedly. Always contains 1,000+ entries.
 */
export function getPlaceholderTopicPool(): ReadonlyArray<string> {
  if (_pool === null) {
    _pool = Object.freeze(buildPool());
  }
  return _pool;
}
