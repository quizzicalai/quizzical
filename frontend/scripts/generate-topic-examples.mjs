import fs from 'node:fs';
import path from 'node:path';

/**
 * Generate 2,000 personality-quiz-plausible topic examples.
 *
 * Each row is a noun phrase intended to read naturally in:
 *   "Which <topic> am I?"
 */

const OUT_PATH = path.resolve('src/data/topicExamples.json');
const TARGET = 2000;

const makeRows = () => {
  const rows = [];
  const seen = new Set();

  const add = (topic, family) => {
    const cleaned = topic.replace(/\s+/g, ' ').trim();
    if (!cleaned) return;
    const key = cleaned.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    rows.push({ topic: cleaned, family });
  };

  const addFromTemplates = (bases, templates, family) => {
    for (const b of bases) {
      for (const t of templates) {
        add(t.includes('{base}') ? t.replaceAll('{base}', b) : `${b} ${t}`, family);
      }
    }
  };

  const franchises = [
    'Star Wars', 'Marvel', 'DC', 'Harry Potter', 'Lord of the Rings', 'Game of Thrones',
    'House of the Dragon', 'The Office', 'Friends', 'Parks and Rec', 'Brooklyn Nine-Nine',
    'The Good Place', 'Stranger Things', 'Breaking Bad', 'Better Call Saul', 'Seinfeld',
    'The Simpsons', 'SpongeBob', 'Adventure Time', 'Avatar', 'Legend of Korra', 'Pokemon',
    'Studio Ghibli', 'Disney', 'Pixar', 'Star Trek', 'Doctor Who', 'The Witcher',
    'Dungeons and Dragons', 'Final Fantasy', 'Zelda', 'Sonic', 'Mario', 'Minecraft',
    'Animal Crossing', 'Stardew Valley', 'Overwatch', 'League of Legends', 'Valorant',
    'Apex Legends', 'Elden Ring', 'Dark Souls', 'Mass Effect', 'Dragon Age', 'Halo',
    'The Last of Us', 'Persona', 'Scooby-Doo', 'Looney Tunes', 'Rick and Morty',
    'BoJack Horseman', 'Community', 'Gilmore Girls', 'Modern Family', 'Schitts Creek',
    'New Girl', 'Ted Lasso', 'Wednesday', 'Bridgerton', 'The Crown', 'The Bear',
    'The White Lotus', 'Sherlock Holmes', 'Percy Jackson', 'Hunger Games', 'Twilight',
    'Greek mythology', 'Norse mythology', 'Roman mythology', 'Egyptian mythology',
  ];
  addFromTemplates(
    franchises,
    [
      '{base} character', '{base} hero', '{base} villain', '{base} sidekick',
      '{base} mentor', '{base} leader', '{base} antihero', '{base} team member',
      '{base} rival', '{base} legend',
    ],
    'Characters',
  );

  const professions = [
    'teacher', 'professor', 'doctor', 'nurse', 'therapist', 'counselor', 'coach',
    'chef', 'baker', 'barista', 'designer', 'illustrator', 'animator', 'writer',
    'journalist', 'editor', 'photographer', 'videographer', 'musician', 'producer',
    'actor', 'director', 'architect', 'engineer', 'developer', 'data analyst',
    'research scientist', 'product manager', 'project manager', 'marketing strategist',
    'sales lead', 'founder', 'entrepreneur', 'lawyer', 'judge', 'mediator', 'detective',
    'police officer', 'firefighter', 'paramedic', 'pilot', 'flight attendant',
    'astronaut', 'mechanic', 'electrician', 'carpenter', 'plumber', 'tailor',
    'fashion stylist', 'makeup artist', 'florist', 'librarian', 'museum curator',
    'park ranger', 'event planner', 'social worker', 'veterinarian', 'zookeeper',
    'game designer', 'streamer', 'content creator',
  ];
  addFromTemplates(
    professions,
    [
      '{base} type', '{base} style', '{base} specialty', '{base} role',
      '{base} approach', '{base} vibe',
    ],
    'Jobs',
  );

  const personalityTopics = [
    'Myers-Briggs type', 'Enneagram type', 'Big Five trait', 'DISC profile',
    'attachment style', 'love language', 'communication style', 'leadership style',
    'conflict style', 'learning style', 'decision-making style', 'problem-solving style',
    'friendship style', 'work style', 'planning style', 'creative style',
    'collaboration style', 'humor style', 'social battery type', 'motivation style',
    'adventure style', 'risk profile', 'focus mode', 'team role',
  ];
  for (const p of personalityTopics) add(p, 'Personality');

  const placeTopics = [
    'city', 'country', 'island', 'beach town', 'mountain town', 'college town',
    'neighborhood', 'road trip destination', 'weekend getaway', 'national park',
    'state park', 'hiking trail', 'camping destination', 'ski resort', 'museum',
    'art gallery', 'bookstore', 'library', 'coffee shop', 'restaurant', 'street market',
    'farmers market', 'music festival', 'film festival', 'theme park', 'castle',
    'palace', 'planet', 'moon', 'constellation', 'galaxy', 'space station',
  ];
  addFromTemplates(
    placeTopics,
    [
      '{base} vibe', '{base} destination',
      '{base} you belong in', '{base} you would live in',
      '{base} energy', '{base} mood', '{base} style',
    ],
    'Places',
  );

  const foodTopics = [
    'pizza', 'pasta', 'ramen', 'sushi', 'taco', 'burger', 'sandwich', 'dumpling',
    'noodle dish', 'soup', 'salad', 'dessert', 'cake', 'cookie', 'pastry',
    'ice cream flavor', 'candy', 'coffee order', 'tea blend', 'smoothie flavor',
    'mocktail', 'cocktail', 'breakfast dish', 'brunch order', 'snack', 'chip flavor',
    'fruit', 'vegetable', 'cheese', 'bread type', 'sauce', 'spice blend',
  ];
  addFromTemplates(
    foodTopics,
    [
      '{base} type', '{base} style', '{base} order',
      '{base} flavor vibe', '{base} comfort pick', '{base} signature',
    ],
    'Food and Drink',
  );

  const animalTopics = [
    'dog breed', 'cat breed', 'bird species', 'sea creature', 'forest animal',
    'desert animal', 'jungle animal', 'dinosaur', 'mythical creature', 'dragon type',
    'horse breed', 'rabbit breed', 'fish species', 'frog species', 'owl type',
    'butterfly type', 'wolf type', 'fox type', 'bear type', 'big cat type',
  ];
  addFromTemplates(
    animalTopics,
    [
      '{base} type', '{base} archetype',
      '{base} energy', '{base} vibe', '{base} style',
    ],
    'Animals',
  );

  const artsAndMedia = [
    'book genre', 'movie genre', 'tv genre', 'anime genre', 'manga genre',
    'music genre', 'album era', 'song vibe', 'playlist mood', 'painting style',
    'art movement', 'photography style', 'dance style', 'fashion aesthetic',
    'interior design style', 'architecture style', 'poetry style', 'writing voice',
    'film trope', 'story archetype',
  ];
  addFromTemplates(
    artsAndMedia,
    [
      '{base} type', '{base} style', '{base} vibe',
      '{base} energy', '{base} era',
    ],
    'Arts and Media',
  );

  const sportsAndGames = [
    'soccer position', 'basketball position', 'baseball role', 'hockey role',
    'volleyball position', 'tennis style', 'boxing style', 'martial arts style',
    'racing style', 'climbing style', 'swimming event', 'track event',
    'Olympic sport', 'board game role', 'card game archetype', 'chess piece',
    'video game class', 'RPG class', 'co-op role', 'strategy game style',
    'puzzle game style',
  ];
  addFromTemplates(
    sportsAndGames,
    ['{base} type', '{base} style', '{base} energy', '{base} role'],
    'Sports and Games',
  );

  const lifestyleAndThings = [
    'weekend hobby', 'creative hobby', 'travel style', 'study style',
    'productivity system', 'journal style', 'workout style', 'home decor style',
    'room aesthetic', 'desk setup', 'backpack type', 'shoe style', 'jacket style',
    'hat style', 'watch style', 'car type', 'bike type', 'camera type',
    'phone app', 'emoji set', 'color palette', 'font style',
  ];
  addFromTemplates(
    lifestyleAndThings,
    ['{base} type', '{base} style', '{base} vibe'],
    'Lifestyle and Things',
  );

  const archetypeBases = [
    'planner', 'night owl', 'early bird', 'book lover', 'movie buff', 'music nerd',
    'foodie', 'traveler', 'gamer', 'athlete', 'artist', 'builder', 'helper',
    'leader', 'problem solver', 'peacekeeper', 'comedian', 'dreamer', 'adventurer',
    'minimalist', 'maximalist', 'trendsetter', 'classic soul', 'romantic',
    'realist', 'optimist', 'strategist', 'storyteller', 'connector',
  ];
  addFromTemplates(
    archetypeBases,
    ['{base} archetype', '{base} vibe', '{base} role'],
    'Archetypes',
  );

  const explicitDistinctionTopics = [
    'Harry Potter house',
    'Harry Potter character',
    'Game of Thrones house',
    'Game of Thrones character',
    'Divergent faction',
    'Hunger Games district',
    'Percy Jackson cabin',
    'Avatar nation',
    'Pokemon starter',
    'Pokemon type',
    'Star Wars faction',
    'Star Wars character',
    'Marvel team',
    'Marvel character',
    'DC team',
    'DC character',
  ];
  for (const t of explicitDistinctionTopics) add(t, 'Characters');

  const bonusBases = [
    'flower', 'tree', 'houseplant', 'gemstone', 'crystal', 'planet', 'constellation',
    'weather pattern', 'season', 'holiday tradition', 'board game', 'card game',
    'party game', 'puzzle type', 'craft project', 'DIY project', 'festival',
    'museum wing', 'library section', 'bookstore section', 'cafe order', 'tea flavor',
    'dessert category', 'comfort food', 'street snack', 'breakfast combo',
    'sandwich category', 'pizza topping', 'pasta shape', 'soup style', 'salad style',
    'ice cream flavor', 'mocktail style', 'cocktail family', 'music playlist',
    'movie marathon', 'book trope', 'story trope', 'character trope', 'anime genre',
    'manga genre', 'comic genre', 'fashion trend', 'outfit formula', 'shoe silhouette',
    'jacket silhouette', 'bag style', 'watch style', 'home decor trend',
    'interior palette', 'workspace setup', 'study routine', 'travel itinerary',
    'road trip playlist', 'weekend plan', 'hiking route', 'camping setup',
    'fitness plan', 'running plan', 'yoga sequence', 'dance routine', 'sports matchup',
    'tennis shot', 'soccer tactic', 'basketball play', 'baseball lineup',
    'hockey line', 'volleyball strategy', 'gaming strategy', 'RPG build',
    'co-op strategy', 'speedrun route', 'photo style', 'camera angle',
    'editing style', 'writing habit', 'journal prompt', 'productivity method',
    'organization system', 'learning technique', 'memory technique', 'debate style',
    'leadership move', 'team ritual', 'friend group dynamic', 'conversation style',
  ];
  const bonusTemplates = [
    '{base} type', '{base} style', '{base} vibe', '{base} archetype',
    '{base} role', '{base} energy', '{base} era', '{base} category',
    '{base} aesthetic', '{base} pick',
  ];
  addFromTemplates(bonusBases, bonusTemplates, 'General Interesting');

  if (rows.length < TARGET) {
    throw new Error(`Generator produced only ${rows.length} rows; increase source banks.`);
  }

  return rows.slice(0, TARGET);
};

const rows = makeRows();
if (rows.length !== TARGET) {
  throw new Error(`Expected ${TARGET} rows, got ${rows.length}`);
}

fs.writeFileSync(OUT_PATH, `${JSON.stringify(rows, null, 2)}\n`, 'utf8');
console.log(`Wrote ${rows.length} examples to ${OUT_PATH}`);
