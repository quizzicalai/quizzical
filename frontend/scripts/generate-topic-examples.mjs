import fs from 'node:fs';
import path from 'node:path';

/**
 * Generate ~2,000+ personality-quiz-plausible topic examples that read
 * naturally inside "Which <topic> am I?".
 *
 * Design rules (to avoid the previous nonsense like "Schitt's Creek mentor",
 * "Gilmore Girls villain", "space station energy", "noodle dish order"):
 *
 *   1. NEVER do unbounded Cartesian product of franchise × generic role.
 *      Each franchise lists ONLY the role-nouns that actually exist in it.
 *
 *   2. NEVER pair concrete categories with abstract suffixes ("energy",
 *      "mood", "vibe", "order", "approach"). Use bare nouns or category-
 *      appropriate templates only.
 *
 *   3. Prefer canonical, googleable phrases that BuzzFeed-style quizzes
 *      use today (e.g. "Hogwarts house", "Pokemon starter", "Disney princess").
 *
 *   4. The padding tier only combines aesthetic adjectives with aesthetic-
 *      friendly nouns (decor, fashion, parties), never with foods,
 *      sports positions, etc.
 */

const OUT_PATH = path.resolve('src/data/topicExamples.json');
const TARGET = 2000;

const seen = new Set();
const rows = [];

const add = (topic, family) => {
  const cleaned = topic.replace(/\s+/g, ' ').trim();
  if (!cleaned) return;
  const key = cleaned.toLowerCase();
  if (seen.has(key)) return;
  seen.add(key);
  rows.push({ topic: cleaned, family });
};

const addAll = (items, family) => {
  for (const t of items) add(t, family);
};

// =====================================================================
// 1) Franchises — per-franchise role lists. Only roles that actually
//    exist in that universe. No generic "mentor / sidekick / antihero"
//    sprayed across every show.
// =====================================================================

const FRANCHISES = [
  // Sci-fi / fantasy with rich faction structure
  { name: 'Star Wars', tags: ['character', 'hero', 'villain', 'Jedi', 'Sith', 'droid', 'species', 'faction', 'pilot', 'bounty hunter'] },
  { name: 'Marvel', tags: ['hero', 'villain', 'Avenger', 'X-Men character', 'mutant power', 'team', 'character'] },
  { name: 'DC', tags: ['hero', 'villain', 'Justice League member', 'Batman ally', 'character'] },
  { name: 'Harry Potter', tags: ['house', 'character', 'professor', 'spell', 'magical creature', 'wand wood', 'Patronus'] },
  { name: 'Lord of the Rings', tags: ['character', 'race', 'fellowship member', 'wizard'] },
  { name: 'Game of Thrones', tags: ['house', 'character', 'sigil'] },
  { name: 'House of the Dragon', tags: ['character', 'dragon'] },
  { name: 'The Witcher', tags: ['character', 'school of witcher', 'monster'] },
  { name: 'Star Trek', tags: ['character', 'crew member', 'species', 'starship'] },
  { name: 'Doctor Who', tags: ['Doctor', 'companion'] },
  { name: 'Avatar: The Last Airbender', tags: ['nation', 'bending style', 'character'] },
  { name: 'The Legend of Korra', tags: ['character', 'bender'] },
  { name: 'Percy Jackson', tags: ['cabin', 'Greek god', 'demigod'] },
  { name: 'Hunger Games', tags: ['district', 'character', 'tribute'] },
  { name: 'Divergent', tags: ['faction'] },
  { name: 'Twilight', tags: ['character', 'vampire clan'] },
  { name: 'The Wheel of Time', tags: ['Ajah', 'character'] },
  { name: 'A Song of Ice and Fire', tags: ['house', 'character'] },
  { name: 'His Dark Materials', tags: ['daemon', 'character'] },
  { name: 'Narnia', tags: ['character', 'creature'] },
  { name: 'Dune', tags: ['house', 'character'] },

  // Sitcoms / dramedies — usually only "character" reads sensibly.
  { name: 'The Office', tags: ['character'] },
  { name: 'Friends', tags: ['character'] },
  { name: 'Parks and Rec', tags: ['character'] },
  { name: 'Brooklyn Nine-Nine', tags: ['character'] },
  { name: 'Community', tags: ['character', 'study group member'] },
  { name: 'Gilmore Girls', tags: ['character'] },
  { name: 'Modern Family', tags: ['character'] },
  { name: "Schitt's Creek", tags: ['character'] },
  { name: 'New Girl', tags: ['character'] },
  { name: 'Ted Lasso', tags: ['character'] },
  { name: 'Seinfeld', tags: ['character'] },
  { name: 'How I Met Your Mother', tags: ['character'] },
  { name: 'The Big Bang Theory', tags: ['character'] },
  { name: 'Cheers', tags: ['character'] },
  { name: 'Frasier', tags: ['character'] },
  { name: 'Curb Your Enthusiasm', tags: ['character'] },
  { name: 'Abbott Elementary', tags: ['character'] },
  { name: 'Only Murders in the Building', tags: ['character'] },
  { name: 'Derry Girls', tags: ['character'] },
  { name: 'Fleabag', tags: ['character'] },
  { name: 'Sex and the City', tags: ['character'] },
  { name: 'Veep', tags: ['character'] },
  { name: 'Arrested Development', tags: ['character'] },
  { name: '30 Rock', tags: ['character'] },

  // Drama series
  { name: 'Stranger Things', tags: ['character'] },
  { name: 'Breaking Bad', tags: ['character'] },
  { name: 'Better Call Saul', tags: ['character'] },
  { name: 'Mad Men', tags: ['character'] },
  { name: 'Succession', tags: ['character'] },
  { name: 'The Bear', tags: ['character'] },
  { name: 'The White Lotus', tags: ['character'] },
  { name: 'The Crown', tags: ['character'] },
  { name: 'Bridgerton', tags: ['character', 'family'] },
  { name: 'Wednesday', tags: ['character'] },
  { name: 'The Sopranos', tags: ['character'] },
  { name: 'Lost', tags: ['character'] },
  { name: 'Yellowstone', tags: ['character'] },
  { name: 'Severance', tags: ['character'] },
  { name: 'The Mandalorian', tags: ['character'] },
  { name: 'House MD', tags: ['character'] },
  { name: "Grey's Anatomy", tags: ['character'] },
  { name: 'Euphoria', tags: ['character'] },
  { name: 'Peaky Blinders', tags: ['character'] },
  { name: 'The Last of Us', tags: ['character'] },
  { name: 'Outlander', tags: ['character'] },

  // Animated TV
  { name: 'The Simpsons', tags: ['character'] },
  { name: 'SpongeBob SquarePants', tags: ['character'] },
  { name: 'Adventure Time', tags: ['character'] },
  { name: 'Rick and Morty', tags: ['character'] },
  { name: 'BoJack Horseman', tags: ['character'] },
  { name: "Bob's Burgers", tags: ['character'] },
  { name: 'Family Guy', tags: ['character'] },
  { name: 'Futurama', tags: ['character'] },
  { name: 'Steven Universe', tags: ['character', 'gem'] },
  { name: 'Gravity Falls', tags: ['character'] },
  { name: 'Looney Tunes', tags: ['character'] },
  { name: 'Scooby-Doo', tags: ['character'] },
  { name: 'Hey Arnold', tags: ['character'] },
  { name: 'Recess', tags: ['character'] },
  { name: 'Arcane', tags: ['character'] },

  // Anime / manga
  { name: 'Naruto', tags: ['character', 'village', 'jutsu type'] },
  { name: 'My Hero Academia', tags: ['character', 'quirk'] },
  { name: 'Demon Slayer', tags: ['character', 'breathing style'] },
  { name: 'Attack on Titan', tags: ['character', 'titan'] },
  { name: 'One Piece', tags: ['character', 'crew member', 'devil fruit'] },
  { name: 'Jujutsu Kaisen', tags: ['character', 'cursed technique'] },
  { name: 'Bleach', tags: ['character'] },
  { name: 'Death Note', tags: ['character'] },
  { name: 'Fullmetal Alchemist', tags: ['character'] },
  { name: 'Spy x Family', tags: ['character'] },
  { name: 'Chainsaw Man', tags: ['character'] },
  { name: 'Sailor Moon', tags: ['Sailor Scout', 'character'] },
  { name: 'Hunter x Hunter', tags: ['character', 'Nen type'] },
  { name: 'Cowboy Bebop', tags: ['character'] },
  { name: 'One Punch Man', tags: ['character'] },

  // Disney / Pixar / Studio Ghibli
  { name: 'Disney', tags: ['princess', 'prince', 'villain', 'sidekick', 'character'] },
  { name: 'Pixar', tags: ['character', 'film'] },
  { name: 'Studio Ghibli', tags: ['character', 'film', 'spirit'] },
  { name: 'DreamWorks', tags: ['character'] },

  // Games
  { name: 'Pokemon', tags: ['starter', 'type', 'legendary', 'gym leader', 'Eeveelution', 'Eevee evolution', 'professor'] },
  { name: 'Zelda', tags: ['character', 'race', 'item'] },
  { name: 'Final Fantasy', tags: ['character', 'class', 'summon'] },
  { name: 'Mario', tags: ['character', 'kart', 'power-up'] },
  { name: 'Sonic', tags: ['character'] },
  { name: 'Minecraft', tags: ['mob', 'biome'] },
  { name: 'Animal Crossing', tags: ['villager', 'island layout'] },
  { name: 'Stardew Valley', tags: ['villager', 'crop'] },
  { name: 'Overwatch', tags: ['hero', 'role'] },
  { name: 'League of Legends', tags: ['champion', 'role'] },
  { name: 'Valorant', tags: ['agent', 'role'] },
  { name: 'Apex Legends', tags: ['legend'] },
  { name: 'Elden Ring', tags: ['class', 'boss', 'weapon'] },
  { name: 'Dark Souls', tags: ['class', 'covenant'] },
  { name: 'Mass Effect', tags: ['character', 'class', 'species'] },
  { name: 'Dragon Age', tags: ['character', 'class'] },
  { name: 'Halo', tags: ['Spartan', 'character'] },
  { name: 'Persona', tags: ['character', 'arcana'] },
  { name: 'Genshin Impact', tags: ['character', 'element'] },
  { name: 'Among Us', tags: ['role'] },
  { name: 'Dungeons and Dragons', tags: ['class', 'race', 'alignment', 'background'] },
  { name: 'Skyrim', tags: ['race', 'class', 'guild'] },
  { name: 'Fallout', tags: ['faction', 'companion'] },
  { name: 'Splatoon', tags: ['weapon class'] },
  { name: 'Smash Bros', tags: ['fighter'] },

  // Movie franchises
  { name: 'Indiana Jones', tags: ['character'] },
  { name: 'James Bond', tags: ['Bond character', 'Bond villain'] },
  { name: 'Mission Impossible', tags: ['character'] },
  { name: 'Fast and Furious', tags: ['character'] },
  { name: 'Sherlock Holmes', tags: ['character'] },
  { name: 'Mean Girls', tags: ['character', 'clique'] },
  { name: 'The Princess Bride', tags: ['character'] },
  { name: 'Clue', tags: ['suspect', 'weapon', 'room'] },
  { name: 'Pitch Perfect', tags: ['Bella'] },
  { name: 'Pirates of the Caribbean', tags: ['character'] },
  { name: 'Jurassic Park', tags: ['dinosaur', 'character'] },

  // Mythology
  { name: 'Greek mythology', tags: ['god', 'goddess', 'monster', 'hero'] },
  { name: 'Norse mythology', tags: ['god', 'goddess', 'creature'] },
  { name: 'Roman mythology', tags: ['god', 'goddess'] },
  { name: 'Egyptian mythology', tags: ['god', 'goddess'] },
  { name: 'Celtic mythology', tags: ['god', 'creature'] },
  { name: 'Japanese mythology', tags: ['yokai', 'kami'] },
];

for (const f of FRANCHISES) {
  for (const t of f.tags) add(`${f.name} ${t}`, 'Characters');
}

// Hand-picked iconic shorthand topics that classic personality quizzes use.
addAll([
  'Hogwarts house', 'Hogwarts professor', 'Sorting Hat result', 'Hogwarts ghost',
  'Hogwarts founder', 'Marauder', 'Patronus animal', 'wand wood',
  'Avatar nation', 'bending style',
  'Disney princess', 'Disney villain', 'Disney prince', 'Disney sidekick',
  'Pixar protagonist',
  'Studio Ghibli protagonist', 'Studio Ghibli spirit',
  'Marvel Avenger', 'X-Men member', 'Spider-Man variant',
  'Justice League member', 'Batman villain', 'Suicide Squad member',
  'Pokemon starter', 'Pokemon type', 'Eeveelution', 'Pokemon legendary',
  'Star Wars Jedi', 'Star Wars Sith', 'Star Wars droid', 'Star Wars bounty hunter',
  'Game of Thrones house', 'A Song of Ice and Fire house',
  'Greek god', 'Greek goddess', 'Norse god', 'Egyptian god',
  'Tolkien race', 'Fellowship member',
  'Pixar movie', 'Disney movie', 'Studio Ghibli movie',
], 'Characters');

// =====================================================================
// 2) Professions — bare nouns; "Which {profession} am I?" reads naturally.
//    No "{profession} approach / vibe / type" sprays.
// =====================================================================

const PROFESSIONS = [
  'teacher', 'professor', 'doctor', 'nurse', 'therapist', 'counselor', 'coach',
  'chef', 'baker', 'pastry chef', 'barista', 'bartender', 'sommelier',
  'designer', 'graphic designer', 'illustrator', 'animator', 'writer',
  'novelist', 'poet', 'screenwriter', 'journalist', 'editor',
  'photographer', 'videographer', 'cinematographer',
  'musician', 'songwriter', 'producer', 'DJ', 'composer', 'conductor',
  'actor', 'director', 'stage manager',
  'architect', 'civil engineer', 'mechanical engineer', 'software engineer',
  'data scientist', 'product manager', 'project manager', 'UX designer',
  'marketing strategist', 'copywriter', 'sales lead', 'founder', 'entrepreneur',
  'lawyer', 'judge', 'mediator', 'detective', 'police officer',
  'firefighter', 'paramedic', 'pilot', 'flight attendant', 'astronaut',
  'mechanic', 'electrician', 'carpenter', 'plumber', 'tailor',
  'fashion designer', 'fashion stylist', 'makeup artist', 'florist',
  'librarian', 'museum curator', 'park ranger', 'event planner',
  'social worker', 'veterinarian', 'zookeeper', 'farmer', 'rancher',
  'game designer', 'streamer', 'content creator', 'podcast host',
  'spy', 'diplomat', 'archaeologist', 'historian',
  'pediatrician', 'surgeon', 'dentist', 'optometrist',
  'gardener', 'park designer', 'landscape architect',
  'travel writer', 'food critic', 'film critic', 'art critic',
];
addAll(PROFESSIONS, 'Jobs');

// =====================================================================
// 3) Personality frameworks — single canonical phrase per concept.
// =====================================================================

addAll([
  'Myers-Briggs type', 'Enneagram type', 'Big Five trait', 'DISC profile',
  'attachment style', 'love language', 'apology language',
  'communication style', 'leadership style', 'conflict style',
  'learning style', 'decision-making style', 'problem-solving style',
  'friendship style', 'work style', 'planning style', 'creative style',
  'collaboration style', 'humor style', 'social battery type',
  'motivation style', 'feedback style',
  'zodiac sign', 'Chinese zodiac animal', 'tarot card', 'Hogwarts house',
  'love type', 'flirting style',
  'risk profile', 'team role', 'meeting role',
], 'Personality');

// =====================================================================
// 4) Places — only safe constructions; never "{place} energy/mood/vibe".
// =====================================================================

const CITIES = [
  'New York', 'Los Angeles', 'Chicago', 'San Francisco', 'Seattle', 'Portland',
  'Austin', 'Nashville', 'New Orleans', 'Miami', 'Boston', 'Washington DC',
  'Toronto', 'Vancouver', 'Montreal',
  'London', 'Paris', 'Rome', 'Barcelona', 'Madrid', 'Berlin', 'Amsterdam',
  'Vienna', 'Prague', 'Copenhagen', 'Stockholm', 'Oslo', 'Reykjavik',
  'Lisbon', 'Dublin', 'Edinburgh',
  'Tokyo', 'Kyoto', 'Osaka', 'Seoul', 'Hong Kong', 'Singapore', 'Bangkok',
  'Shanghai', 'Mumbai', 'Delhi', 'Istanbul', 'Dubai',
  'Sydney', 'Melbourne', 'Auckland',
  'Mexico City', 'Buenos Aires', 'Rio de Janeiro', 'Lima', 'Bogota',
  'Cape Town', 'Marrakech', 'Cairo', 'Nairobi',
];
for (const c of CITIES) add(`${c} neighborhood`, 'Places');
for (const c of CITIES) add(`${c} café`, 'Places');

addAll([
  'world city to live in', 'European city to live in', 'Asian city to visit',
  'Latin American city to visit',
  'beach destination', 'mountain town', 'college town', 'island getaway',
  'national park', 'state park', 'world heritage site',
  'road trip route', 'weekend getaway', 'romantic destination',
  'foodie destination', 'arts city', 'music city', 'fashion capital',
  'New York neighborhood', 'Paris arrondissement', 'Tokyo neighborhood',
  'London borough', 'Brooklyn neighborhood', 'LA neighborhood',
  'planet in our solar system', 'fictional planet',
  'Hogwarts common room', 'Hogwarts classroom',
  'fictional city to live in',
  'national park to visit', 'state to road trip',
], 'Places');

// =====================================================================
// 5) Food and drink — bare nouns plus a few canonical "style" phrases.
// =====================================================================

addAll([
  'pizza style', 'pasta shape', 'ramen style', 'sushi roll', 'taco style',
  'burger style', 'sandwich', 'dumpling', 'noodle dish', 'soup',
  'salad', 'dessert', 'cake', 'cookie', 'pastry', 'donut',
  'ice cream flavor', 'gelato flavor', 'candy bar',
  'coffee order', 'tea blend', 'matcha drink', 'smoothie', 'juice blend',
  'mocktail', 'cocktail', 'wine variety', 'craft beer style',
  'breakfast dish', 'brunch order', 'snack', 'chip flavor',
  'fruit', 'vegetable', 'cheese', 'bread', 'sauce', 'spice',
  'BBQ style', 'taco truck order', 'street food',
  'Thanksgiving side', 'holiday cookie',
  'comfort food', 'guilty pleasure snack',
  'condiment', 'salsa', 'hot sauce', 'salad dressing',
  'Italian dish', 'Mexican dish', 'Japanese dish', 'Chinese dish',
  'Indian dish', 'Thai dish', 'Korean dish', 'French dish', 'Spanish dish',
  'Greek dish', 'Lebanese dish', 'Vietnamese dish', 'Ethiopian dish',
  'breakfast cereal', 'pancake topping', 'waffle topping',
  'coffee shop pastry', 'coffee shop drink', 'boba order',
  'late-night snack', 'roadtrip snack', 'movie theater snack',
  'birthday cake flavor', 'wedding cake style',
], 'Food and Drink');

// =====================================================================
// 6) Animals — bare nouns or "type"/"breed"/"species".
// =====================================================================

addAll([
  'dog breed', 'cat breed', 'bird species', 'sea creature', 'forest animal',
  'desert animal', 'jungle animal', 'savanna animal', 'arctic animal',
  'dinosaur', 'mythical creature', 'dragon type',
  'horse breed', 'rabbit breed', 'fish species', 'frog species',
  'owl species', 'butterfly species', 'wolf', 'fox', 'bear', 'big cat',
  'shark species', 'whale species', 'octopus species', 'penguin species',
  'parrot species', 'snake species', 'lizard species', 'crab species',
  'spider species',
  'house pet', 'farm animal', 'zoo animal', 'aquarium animal',
  'cryptid', 'sea monster',
  'cattle breed', 'goat breed', 'sheep breed', 'chicken breed',
  'duck species', 'reptile pet',
], 'Animals');

// =====================================================================
// 7) Arts & Media — canonical bare nouns.
// =====================================================================

addAll([
  'book genre', 'movie genre', 'TV genre', 'anime genre', 'manga genre',
  'music genre', 'subgenre of rock', 'subgenre of hip-hop', 'subgenre of pop',
  'classical era', 'painting style', 'art movement', 'photography style',
  'dance style', 'fashion aesthetic', 'interior design style',
  'architecture style', 'poetry style', 'film trope', 'story archetype',
  'Pixar movie', 'Disney movie', 'Studio Ghibli movie',
  'Wes Anderson movie', 'Quentin Tarantino movie', 'Christopher Nolan movie',
  'Hitchcock film', 'horror subgenre', 'rom-com trope',
  'Shakespeare play', 'classic novel', 'dystopian novel',
  'Pixar short', 'animated short', 'Marvel film', 'DC film',
  'video game soundtrack',
  'Broadway musical', 'opera', 'ballet',
  'museum wing', 'gallery style',
  'photography genre',
  'jazz subgenre', 'metal subgenre', 'EDM subgenre', 'indie subgenre',
  'Studio Ghibli soundtrack',
  'classic rock band', '90s sitcom', '2000s sitcom',
  'Olivia Rodrigo song', 'Taylor Swift album', 'Beyoncé era',
  'Drake album', 'Lady Gaga era', 'Madonna era',
  'BTS member', 'One Direction member', 'Spice Girl',
  'Beatles member', 'Rolling Stones member', 'Queen member',
  'Saturday Night Live cast member',
  'Real Housewives city',
  'Met Gala theme',
  'Drag Race queen', 'reality TV villain',
], 'Arts and Media');

// =====================================================================
// 8) Sports & games — only sensible bare nouns. No "racing style energy".
// =====================================================================

addAll([
  'soccer position', 'basketball position', 'baseball position',
  'hockey position', 'volleyball position', 'football position',
  'tennis playing style', 'boxing style', 'martial arts style',
  'racing discipline', 'climbing discipline', 'swimming stroke',
  'track event', 'field event', 'Olympic sport', 'Winter Olympic sport',
  'board game', 'card game', 'party game', 'puzzle game',
  'chess piece', 'chess opening',
  'video game class', 'RPG class', 'co-op role', 'strategy game style',
  'D&D class', 'D&D race', 'D&D alignment', 'D&D background',
  'Monopoly token', 'Clue suspect', 'Risk strategy',
  'fantasy football role', 'pickup basketball role',
  'tennis Grand Slam', 'golf shot',
  'CrossFit move', 'yoga pose', 'pilates move',
], 'Sports and Games');

// =====================================================================
// 9) Lifestyle & things — bare nouns.
// =====================================================================

addAll([
  'weekend hobby', 'creative hobby', 'travel style', 'study style',
  'productivity system', 'journal style', 'workout style',
  'home decor style', 'room aesthetic', 'desk setup',
  'backpack style', 'shoe style', 'jacket style', 'hat style',
  'watch style', 'sunglasses style',
  'car type', 'bike type', 'camera type', 'phone aesthetic',
  'emoji set', 'color palette', 'font style',
  'plant for your room', 'houseplant', 'flower bouquet',
  'morning routine', 'evening ritual', 'commute style',
  'sleep schedule', 'coffee shop order', 'bookstore section',
  'streaming queue', 'reading list',
  'pet for your lifestyle',
  'hairstyle', 'tattoo style', 'nail art style',
  'minimalist aesthetic', 'maximalist aesthetic',
  'cottagecore aesthetic', 'dark academia aesthetic',
  'coastal grandmother aesthetic', 'soft girl aesthetic', 'goblincore aesthetic',
  'Y2K fashion era', '90s fashion era', '70s fashion era', '80s fashion era',
  'bedroom aesthetic', 'kitchen aesthetic', 'living room aesthetic',
  'wedding theme', 'birthday party theme', 'Halloween costume',
  'New Year resolution', 'summer plan', 'winter plan',
  'roadtrip playlist', 'study playlist', 'workout playlist',
  'thrift store find', 'farmers market find',
  'coffee table book', 'cookbook style',
  'bullet journal style', 'planner style',
  'gym class', 'fitness app',
], 'Lifestyle and Things');

// =====================================================================
// 10) Archetypes — fun stand-alone tags (always paired with "archetype"
//     or "energy" so the phrase reads naturally).
// =====================================================================

const ARCHETYPE_BASES = [
  'planner', 'night owl', 'early bird', 'book lover', 'movie buff', 'music nerd',
  'foodie', 'traveler', 'gamer', 'athlete', 'artist', 'builder',
  'helper', 'leader', 'problem solver', 'peacekeeper', 'comedian', 'dreamer',
  'adventurer', 'minimalist', 'maximalist', 'trendsetter', 'romantic',
  'realist', 'optimist', 'strategist', 'storyteller', 'connector',
  'big sister', 'cool aunt', 'wise mentor', 'best friend',
  'hype person', 'chaos agent', 'group therapist', 'group historian',
];
for (const a of ARCHETYPE_BASES) add(`${a} archetype`, 'Archetypes');
addAll([
  'main character energy', 'side character energy', 'supporting cast energy',
  'comic relief role', 'mom-friend role', 'dad-friend role',
], 'Archetypes');

// =====================================================================
// 11) Pop-culture / "group dynamic" specifics that quizzes love.
// =====================================================================

addAll([
  'Friends couple', 'Office romance',
  'Dunder Mifflin role',
  'Bridgerton sibling',
  'Kardashian sister', 'Jenner sister',
  'Olympic event', 'Winter Olympic event',
  'Survivor archetype', 'Big Brother archetype',
  'Bachelor contestant trope',
  'famous mustache', 'famous wig', 'famous on-screen kiss',
  'wedding role', 'bridesmaid duty',
  'roadtrip role', 'group chat role', 'group project role',
  'Thanksgiving guest archetype', 'Friendsgiving role',
  'birthday party role', 'concert friend group role',
  'hostel roommate archetype',
  'office Secret Santa giver',
  'book club member',
  'fantasy football league member',
  'wedding guest', 'wedding speech style',
  'YA dystopia heroine', 'YA fantasy hero',
  'Avengers founding member', 'X-Men founding member',
], 'Pop Culture');

// =====================================================================
// 12) Generic single-word "which X" staples (always read fine).
// =====================================================================

const SAFE_SINGLE_TOPICS = [
  'color', 'pastel color', 'neon color', 'jewel tone',
  'season', 'birthstone', 'gemstone', 'crystal',
  'flower', 'tree', 'herb', 'spice', 'plant', 'fruit', 'vegetable',
  'bread', 'cheese', 'pasta', 'rice dish', 'grain',
  'cocktail', 'mocktail', 'wine', 'beer', 'tea', 'coffee drink',
  'breakfast', 'lunch', 'dinner', 'dessert', 'snack',
  'pop song', 'rock anthem',
  'romcom', 'thriller subgenre', 'horror trope', 'sitcom',
  'reality show', 'cooking show', 'crime drama', 'medical drama',
  'board game', 'card game', 'video game', 'arcade game',
  'puzzle', 'sport', 'extreme sport',
  'wedding theme', 'birthday party theme', 'Halloween costume',
  'classic novel', 'modern classic', 'memoir', 'comic book',
  'fantasy novel', 'sci-fi novel',
  'app aesthetic', 'startup vibe', 'work-from-home setup',
  'streaming platform', 'podcast genre',
  'concert venue', 'festival',
  'museum', 'art gallery', 'bookstore section',
  'trail name', 'campground style',
  'kitchen gadget', 'office supply',
];
addAll(SAFE_SINGLE_TOPICS, 'General');

// =====================================================================
// 13) Aesthetic-only padding tier.
//    AESTHETIC_PREFIXES x AESTHETIC_NOUNS — these always read as a
//    sensible adjective+noun combo. We never combine adjectives with
//    foods, sports positions, jobs, etc.
// =====================================================================

const AESTHETIC_PREFIXES = [
  'classic', 'modern', 'vintage', 'minimalist', 'maximalist',
  'cozy', 'luxe', 'rustic', 'quirky', 'eclectic',
  'retro', 'futuristic', 'whimsical', 'elegant', 'playful',
  'romantic', 'moody', 'cheerful', 'soft', 'bold',
  'industrial', 'bohemian', 'preppy', 'gothic', 'sporty',
  'monochrome', 'pastel', 'neon', 'jewel-tone', 'earthy',
];

const AESTHETIC_NOUNS = [
  'aesthetic', 'home decor style', 'room aesthetic', 'kitchen aesthetic',
  'bedroom aesthetic', 'living room style', 'office setup',
  'café vibe', 'restaurant vibe', 'wedding style',
  'engagement ring style', 'birthday party theme',
  'fashion era', 'fashion aesthetic', 'jewelry style', 'shoe style',
  'jacket style', 'coat style', 'hat style', 'sunglasses style',
  'watch style', 'bag style', 'scarf style', 'belt style',
  'haircut', 'tattoo style', 'nail art', 'makeup look',
  'desktop wallpaper', 'phone aesthetic', 'app icon style',
  'workspace style', 'desk setup', 'stationery style',
  'car interior', 'travel itinerary', 'vacation style',
  'cocktail style', 'cafe order', 'wedding cake style',
  'birthday cake style', 'dinner party style',
  'reading nook style', 'library style', 'gallery style',
  'stationery aesthetic', 'planner aesthetic',
  'gift wrap style', 'tablescape style', 'cocktail party style',
  'bridesmaid style', 'groomsmen style',
  'engagement photo style', 'maternity photo style',
  'wedding registry style', 'baby shower theme',
  'graduation party theme', 'housewarming theme',
  'bookshelf style', 'gallery wall style',
  'porch style', 'balcony aesthetic', 'patio aesthetic',
  'garden style', 'backyard aesthetic',
  'coffee bar style', 'home bar style',
  'reading list', 'movie marathon theme',
  'roadtrip aesthetic', 'campsite aesthetic',
  'study spot aesthetic', 'studio aesthetic',
  'pet bed aesthetic', 'kids room aesthetic',
];

for (const a of AESTHETIC_PREFIXES) {
  for (const n of AESTHETIC_NOUNS) {
    if (rows.length >= TARGET) break;
    add(`${a} ${n}`, 'Aesthetic');
  }
  if (rows.length >= TARGET) break;
}

// =====================================================================
// Final sanity check + write
// =====================================================================

if (rows.length < TARGET) {
  throw new Error(
    `Generator produced only ${rows.length} rows (need >= ${TARGET}); expand source banks.`,
  );
}

const final = rows.slice(0, TARGET);
fs.writeFileSync(OUT_PATH, `${JSON.stringify(final, null, 2)}\n`, 'utf8');
console.log(`Wrote ${final.length} examples to ${OUT_PATH}`);
