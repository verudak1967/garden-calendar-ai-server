// Канонический справочник видов и групп (модель данных v2).
// SpeciesCatalog = статический каталог здесь + пользовательские виды
// (species_custom в IndexedDB). Группа вида фиксируется один раз —
// все записи одного вида всегда попадают в одну группу.

import { icon } from './icons.js';

export const GROUPS = {
  '01': { name: 'Плодовые деревья', icon: 'tree' },
  '02': { name: 'Ягодные', icon: 'berry' },
  '03': { name: 'Овощные и зелёные', icon: 'carrot' },
  '04': { name: 'Декоративные', icon: 'flower' },
  '05': { name: 'Комнатные', icon: 'pot' },
};

export const SUBGROUPS = {
  '01.01': { name: 'Семечковые', group: '01' },
  '01.02': { name: 'Косточковые', group: '01' },
  '02.01': { name: 'Ягодные кустарники', group: '02' },
  '02.02': { name: 'Полукустарники и травянистые', group: '02' },
  '02.03': { name: 'Лианы', group: '02' },
  '03.01': { name: 'Плодовые овощи', group: '03' },
  '03.02': { name: 'Корнеплоды и клубнеплоды', group: '03' },
  '03.03': { name: 'Капустные', group: '03' },
  '03.04': { name: 'Салатные и пряные', group: '03' },
  '04.01': { name: 'Декоративные кустарники', group: '04' },
  '04.02': { name: 'Травянистые многолетники', group: '04' },
  '04.03': { name: 'Луковичные', group: '04' },
  '04.04': { name: 'Однолетники и двулетники', group: '04' },
  '05.01': { name: 'Декоративнолиственные', group: '05' },
  '05.02': { name: 'Красивоцветущие', group: '05' },
  '05.03': { name: 'Суккуленты и кактусы', group: '05' },
  '05.04': { name: 'Ампельные и лианы', group: '05' },
};

// [key, название, латынь, группа, подгруппа, семейство, templateId]
const S = [
  ['apple', 'Яблоня', 'Malus domestica', '01', '01.01', 'Rosaceae'],
  ['pear', 'Груша', 'Pyrus', '01', '01.01', 'Rosaceae'],
  ['cherry', 'Вишня', 'Prunus cerasus', '01', '01.02', 'Rosaceae'],
  ['plum', 'Слива', 'Prunus domestica', '01', '01.02', 'Rosaceae'],
  ['sweet_cherry', 'Черешня', 'Prunus avium', '01', '01.02', 'Rosaceae'],
  ['apricot', 'Абрикос', 'Prunus armeniaca', '01', '01.02', 'Rosaceae'],
  ['myrobalan', 'Алыча', 'Prunus cerasifera', '01', '01.02', 'Rosaceae'],
  ['currant', 'Смородина', 'Ribes', '02', '02.01', 'Grossulariaceae'],
  ['gooseberry', 'Крыжовник', 'Ribes uva-crispa', '02', '02.01', 'Grossulariaceae'],
  ['honeysuckle', 'Жимолость', 'Lonicera', '02', '02.01', 'Caprifoliaceae'],
  ['blueberry', 'Голубика', 'Vaccinium', '02', '02.01', 'Ericaceae'],
  ['blackberry', 'Ежевика', 'Rubus', '02', '02.01', 'Rosaceae'],
  ['sea_buckthorn', 'Облепиха', 'Hippophae rhamnoides', '02', '02.01', 'Elaeagnaceae'],
  ['irga', 'Ирга', 'Amelanchier', '02', '02.01', 'Rosaceae'],
  ['barberry', 'Барбарис', 'Berberis', '02', '02.01', 'Berberidaceae'],
  ['raspberry', 'Малина', 'Rubus idaeus', '02', '02.02', 'Rosaceae'],
  ['strawberry', 'Клубника', 'Fragaria ananassa', '02', '02.02', 'Rosaceae'],
  ['grape', 'Виноград', 'Vitis', '02', '02.03', 'Vitaceae'],
  ['actinidia', 'Актинидия', 'Actinidia', '02', '02.03', 'Actinidiaceae'],
  ['tomato', 'Томат', 'Solanum lycopersicum', '03', '03.01', 'Паслёновые', 'tomato'],
  ['cucumber', 'Огурец', 'Cucumis sativus', '03', '03.01', 'Тыквенные', 'cucumber'],
  ['pepper', 'Перец сладкий', 'Capsicum annuum', '03', '03.01', 'Паслёновые', 'pepper'],
  ['eggplant', 'Баклажан', 'Solanum melongena', '03', '03.01', 'Паслёновые', 'eggplant'],
  ['zucchini', 'Кабачок', 'Cucurbita pepo', '03', '03.01', 'Тыквенные', 'zucchini'],
  ['patisson', 'Патиссон', 'Cucurbita pepo var. patisson', '03', '03.01', 'Тыквенные', 'patisson'],
  ['pumpkin', 'Тыква', 'Cucurbita', '03', '03.01', 'Тыквенные', 'pumpkin'],
  ['peas', 'Горох', 'Pisum sativum', '03', '03.01', 'Бобовые', 'peas'],
  ['beans', 'Фасоль', 'Phaseolus', '03', '03.01', 'Бобовые', 'beans'],
  ['carrot', 'Морковь', 'Daucus carota', '03', '03.02', 'Зонтичные', 'carrot'],
  ['potato', 'Картофель', 'Solanum tuberosum', '03', '03.02', 'Паслёновые', 'potato'],
  ['beetroot', 'Свёкла', 'Beta vulgaris', '03', '03.02', 'Маревые', 'beetroot'],
  ['radish', 'Редис', 'Raphanus sativus', '03', '03.02', 'Крестоцветные', 'radish'],
  ['turnip', 'Редька, репа, брюква', 'Brassica rapa', '03', '03.02', 'Крестоцветные', 'turnip'],
  ['garlic', 'Чеснок', 'Allium sativum', '03', '03.02', 'Лилейные (Амариллисовые)', 'garlic'],
  ['onion', 'Лук репчатый', 'Allium cepa', '03', '03.02', 'Лилейные (Амариллисовые)', 'onion'],
  ['cabbage', 'Капуста', 'Brassica oleracea', '03', '03.03', 'Крестоцветные'],
  ['lettuce', 'Салат', 'Lactuca sativa', '03', '03.04', 'Астровые', 'lettuce'],
  ['dill', 'Укроп', 'Anethum graveolens', '03', '03.04', 'Зонтичные', 'greens'],
  ['parsley', 'Петрушка', 'Petroselinum', '03', '03.04', 'Зонтичные', 'greens'],
  ['basil', 'Базилик', 'Ocimum basilicum', '03', '03.04', 'Яснотковые', 'greens'],
  ['lilac', 'Сирень', 'Syringa', '04', '04.01'],
  ['hydrangea', 'Гортензия', 'Hydrangea', '04', '04.01'],
  ['rose_garden', 'Роза садовая', 'Rosa', '04', '04.01'],
  ['peony', 'Пион', 'Paeonia', '04', '04.02'],
  ['phlox', 'Флокс', 'Phlox', '04', '04.02'],
  ['iris', 'Ирис', 'Iris', '04', '04.02'],
  ['daisy', 'Ромашка (нивяник)', 'Leucanthemum', '04', '04.02'],
  ['hosta', 'Хоста', 'Hosta', '04', '04.02'],
  ['astilbe', 'Астильба', 'Astilbe', '04', '04.02'],
  ['daylily', 'Лилейник', 'Hemerocallis', '04', '04.02'],
  ['calla', 'Калла', 'Zantedeschia', '04', '04.02'],
  ['tulip', 'Тюльпан', 'Tulipa', '04', '04.03'],
  ['lily', 'Лилия', 'Lilium', '04', '04.03'],
  ['marigold', 'Бархатцы', 'Tagetes', '04', '04.04'],
  ['aster', 'Астра', 'Callistephus', '04', '04.04'],
  ['petunia', 'Петуния', 'Petunia', '04', '04.04'],
  ['cosmea', 'Космея', 'Cosmos', '04', '04.04'],
  ['nasturtium', 'Настурция', 'Tropaeolum', '04', '04.04'],
  ['dianthus', 'Гвоздика', 'Dianthus', '04', '04.04'],
  ['begonia', 'Бегония садовая', 'Begonia', '04', '04.04'],
  ['sansevieria', 'Сансевиерия', 'Sansevieria', '05', '05.01'],
  ['zamioculcas', 'Замиокулькас', 'Zamioculcas', '05', '05.01'],
  ['chlorophytum', 'Хлорофитум', 'Chlorophytum', '05', '05.01'],
  ['ficus_benjamina', 'Фикус Бенджамина', 'Ficus benjamina', '05', '05.01'],
  ['ficus_elastica', 'Фикус каучуконосный', 'Ficus elastica', '05', '05.01'],
  ['dracaena', 'Драцена', 'Dracaena', '05', '05.01'],
  ['fern', 'Папоротник', 'Polypodiopsida', '05', '05.01'],
  ['calathea', 'Калатея', 'Calathea', '05', '05.01'],
  ['orchid', 'Орхидея фаленопсис', 'Phalaenopsis', '05', '05.02'],
  ['spathiphyllum', 'Спатифиллум', 'Spathiphyllum', '05', '05.02'],
  ['rose_indoor', 'Комнатная роза', 'Rosa', '05', '05.02'],
  ['anthurium', 'Антуриум', 'Anthurium', '05', '05.02'],
  ['pelargonium', 'Герань (пеларгония)', 'Pelargonium', '05', '05.02'],
  ['gloxinia', 'Глоксиния', 'Sinningia', '05', '05.02'],
  ['strelitzia', 'Стрелиция', 'Strelitzia', '05', '05.02'],
  ['schlumbergera', 'Декабрист', 'Schlumbergera', '05', '05.02'],
  ['aloe', 'Алоэ', 'Aloe', '05', '05.03'],
  ['cactus', 'Кактус / суккулент', 'Cactaceae', '05', '05.03'],
  ['crassula', 'Толстянка', 'Crassula', '05', '05.03'],
  ['tradescantia', 'Традесканция', 'Tradescantia', '05', '05.04'],
  ['cissus', 'Циссус', 'Cissus', '05', '05.04'],
  ['hoya', 'Хойя', 'Hoya', '05', '05.04'],
];

export const SPECIES = S.map(([k, name, latin, g, s, family, templateId]) => ({
  k, name, latin, g, s, family: family || null, templateId: templateId || null,
}));

const BY_NAME = new Map(SPECIES.map((sp) => [sp.name.toLowerCase(), sp]));

export function groupLabel(code) { return GROUPS[code] ? GROUPS[code].name : ''; }
// SVG-иконка группы в стиле нижней панели (size регулируется местом вызова)
export function groupIconHTML(code, size = 22) {
  return GROUPS[code] ? icon(GROUPS[code].icon, size) : icon('sprout', size);
}
export function subgroupLabel(code) { return SUBGROUPS[code] ? SUBGROUPS[code].name : ''; }

// Поиск вида по имени: точное совпадение → вхождение (порядок «Томаты»/«Томат»).
export function findSpecies(name) {
  if (!name) return null;
  const q = String(name).toLowerCase().trim();
  if (BY_NAME.has(q)) return BY_NAME.get(q);
  let best = null;
  for (const sp of SPECIES) {
    if (sp.name.toLowerCase() === q) return sp;
    // «Томаты» → «Томат», «Огурцы» → «Огурец»: совпадение по основе (мин. 5 символов)
    const base = Math.min(sp.name.length, q.length);
    if (base >= 5 && (sp.name.toLowerCase().startsWith(q.slice(0, base)) || q.startsWith(sp.name.toLowerCase().slice(0, base)))) {
      if (!best || sp.name.length < best.name.length) best = sp;
    }
  }
  return best;
}

export function speciesByKey(key) {
  return SPECIES.find((sp) => sp.k === key) || null;
}

// Локации по умолчанию (создаются при миграции; type из модели v2)
export const DEFAULT_LOCATIONS = [
  { id: 'loc-open', name: 'Открытый грунт', type: 'OPEN_GROUND' },
  { id: 'loc-green', name: 'Теплица', type: 'GREENHOUSE' },
  { id: 'loc-room', name: 'Дом', type: 'ROOM' },
  { id: 'loc-balcony', name: 'Балкон', type: 'BALCONY' },
  { id: 'loc-seed', name: 'Рассада', type: 'SEEDLING' },
];

// Системные теги (модель v2, п. 5 промпта; климатические — задел
// под модификацию usdaZoneToDayShift)
export const SYSTEM_TAGS = [
  { code: 'greenhouse', name: 'Теплица', type: 'SYSTEM' },
  { code: 'open_ground', name: 'Открытый грунт', type: 'SYSTEM' },
  { code: 'seedling', name: 'Рассада', type: 'SYSTEM' },
  { code: 'cover', name: 'Требует укрытия', type: 'SYSTEM' },
  { code: 'hardy', name: 'Морозостойкое', type: 'SYSTEM' },
  { code: 'early', name: 'Ранний срок', type: 'MATURITY' },
  { code: 'mid', name: 'Средний срок', type: 'MATURITY' },
  { code: 'late', name: 'Поздний срок', type: 'MATURITY' },
  { code: 'frost_return', name: 'Возвратные заморозки', type: 'CLIMATE' },
  { code: 'short_season', name: 'Короткий сезон', type: 'CLIMATE' },
  { code: 'exotic', name: 'Экзотика', type: 'SYSTEM' },
  { code: 'family_solanaceae', name: 'Семейство: паслёновые', type: 'FAMILY' },
  { code: 'family_cucurbitaceae', name: 'Семейство: тыквенные', type: 'FAMILY' },
  { code: 'family_brassicaceae', name: 'Семейство: крестоцветные', type: 'FAMILY' },
  { code: 'family_fabaceae', name: 'Семейство: бобовые', type: 'FAMILY' },
  { code: 'family_apiaceae', name: 'Семейство: зонтичные', type: 'FAMILY' },
  { code: 'family_amaranthaceae', name: 'Семейство: маревые', type: 'FAMILY' },
  { code: 'family_allium', name: 'Семейство: лилейные', type: 'FAMILY' },
  { code: 'family_rosaceae', name: 'Семейство: розоцветные', type: 'FAMILY' },
];
