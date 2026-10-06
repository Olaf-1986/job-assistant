from __future__ import annotations

import re
import unicodedata
from collections import Counter

# Offline, deterministic evidence only. A missing match is unknown, never implicitly English.
# Function words complement job vocabulary so English headings/technical terms cannot dominate
# an otherwise unidentified description. Foreign-language evidence is checked independently.
_ACCEPTED_WORDS = {
    "en": frozenset(
        (
            "the and with for from your you our we are is be will have has this that to of in on as an a "
            "at or by about who can their they it work working experience requirements required responsibilities "
            "skills analysis analyze analyst business systems system role position team join looking knowledge "
            "strong ability excellent ensure support development develop design build management manage "
            "gathering stories acceptance criteria stakeholder stakeholders functional technical documentation "
            "processes process integrations integration administration administer workflows permissions custom "
            "fields configure configuration own maintain maintenance implement implementation collaborate "
            "communication delivery context remote hybrid office international distributed worldwide "
            "english user users use must including include based years salary benefits applications candidates "
            "services engineering"
        ).split()
    ),
    "ru": frozenset(
        (
            "и в на с по для из не мы вы это от к или у о а как до будет быть что работы работа "
            "опыт требования обязанности условия знания знание навыки анализ аналитик системный бизнес "
            "требований документация документации интеграции интеграций разработка разработки разработке "
            "процессов процессы проектирование описание описания работаешь работать командой команда "
            "команду компании компания ищем требуется удаленно удаленная удаленной всем миру "
            "систем системами данных заказчиком пользователями взаимодействие ведение подготовка"
        ).split()
    ),
    "es": frozenset(
        (
            "el la los las y de del para con en un una que por se su sus tu tus es al como "
            "requisitos experiencia responsabilidades habilidades trabajo empresa equipo buscamos "
            "conocimientos conocimiento analisis analista negocio negocios sistemas procesos requisitos "
            "documentacion colaborar desarrollar desarrollo gestion comunicacion remoto remota "
            "ofrecemos capacidad experiencia tecnico tecnica funcionales usuarios interesados "
            "nuestro nuestra nuestros nuestras tienes ser hacer trabajar salario beneficios"
        ).split()
    ),
}
_OTHER_WORDS = {
    "pl": "wymagania doswiadczenie obowiazki umiejetnosci praca firma oraz jest sie dla nas nasze "
    "naszych twoje twoich zakres poszukujemy szukamy bedziesz bedzie oferujemy znajomosc "
    "wspolpraca zespol zespolu stanowisko zatrudnienie wynagrodzenie ktory ktora ktore klienta",
    "de": "und oder mit fur nicht deutsch deutschkenntnisse kenntnisse erfahrung aufgaben anforderungen "
    "bewerbung kunden unternehmen entwicklung wir sie ihre unseren unser eine einen der die das",
    "fr": "exigences responsabilites competences travail entreprise nous vous votre notre avec pour "
    "recherchons equipe poste dans des les une du est sont missions connaissance francais",
    "it": "requisiti esperienza responsabilita competenze lavoro azienda cerchiamo conoscenza "
    "della delle degli siamo nostri nostra attivita candidato gestione nella sono lavorare",
    "pt": "experiencia competencias trabalho nossa nossos voce voces uma habilidades buscamos "
    "conhecimento conhecimentos trabalhar desenvolvimento gestao comunicacao requisitos",
    "nl": "vereisten ervaring verantwoordelijkheden vaardigheden werk bedrijf wij zijn zoeken "
    "jouw onze kennis het een voor van met deze functie bieden werkzaamheden",
    "uk": "досвід вимоги обовязки навички знання робота шукаємо працювати команди розробка "
    "взаємодія бізнесу аналіз та що для від які який",
    "be": "вопыт патрабаванні абавязкі навыкі веды праца шукаем распрацоўка каманды бізнесу",
    "bg": "изисквания отговорности умения опит търсим работа познания екип нашите вашите "
    "разработка анализ бизнес процеси данни",
    "cs": "pozadavky zkusenosti odpovednosti dovednosti prace hledame znalost nabizime spolecnost "
    "nasich vasich spoluprace tvorba",
    "sk": "poziadavky skusenosti zodpovednosti zrucnosti praca hladame znalost ponukame spolocnost",
    "ro": "cerinte experienta responsabilitati abilitati lucru cautam cunostinte echipa pentru "
    "dezvoltare companie suntem noastra",
    "tr": "gereksinimler deneyim sorumluluklar beceriler sirket ariyoruz bilgi calisma gorevler "
    "icin olarak olan ekip yonetimi",
    "sv": "krav erfarenhet ansvar kunskaper foretag arbetsuppgifter soker vara din du och att inom",
    "da": "krav erfaring ansvar faerdigheder virksomhed arbejdsopgaver soger vores dine og til",
    "no": "krav erfaring ansvar ferdigheter selskap arbeidsoppgaver soker vare dine og til",
    "fi": "vaatimukset kokemus vastuut taidot yritys etsimme tehtavat osaaminen tyo kanssa sinulla",
    "hu": "kovetelmenyek tapasztalat felelossegek keszsegek vallalat keresunk ismerete feladatok munka",
    "id": "persyaratan pengalaman tanggung jawab keterampilan pekerjaan perusahaan kami mencari "
    "dengan untuk yang dan dalam kemampuan",
}
# Shared words (e.g. Spanish/Portuguese or Russian/Ukrainian) cannot establish a foreign language.
_SUPPORTED_WORDS = frozenset().union(*_ACCEPTED_WORDS.values())
_OTHER_WORDS = {code: frozenset(words.split()) - _SUPPORTED_WORDS for code, words in _OTHER_WORDS.items()}
_POLISH_STEMS = re.compile(
    r"(?:wymaga|doswiadcz|obowiazk|umiejetnos|znajomos|pracow|wspolprac|poszuk|zatrudni|"
    r"wynagrodz|analityk|wdroz|zespol|biznesow|projektow|rozwij|tworz|dokumentacj)[a-z]*"
)
ALLOWED_REQUIRED_LANGUAGES = frozenset({"English", "Russian", "Spanish"})
LANGUAGE_NAME_PATTERNS = {
    "English": r"english|английск[\wё]*",
    "Russian": r"russian|русск[\wё]*",
    "Spanish": r"spanish|español|испанск[\wё]*",
    "Afrikaans": r"afrikaans|африкаанс",
    "Albanian": r"albanian|албанск[\wё]*",
    "Arabic": r"arabic|арабск[\wё]*",
    "Armenian": r"armenian|армянск[\wё]*",
    "Azerbaijani": r"azerbaijani|азербайджанск[\wё]*",
    "Belarusian": r"belarusian|белорусск[\wё]*",
    "Bengali": r"bengali|bangla|бенгальск[\wё]*",
    "Bosnian": r"bosnian|боснийск[\wё]*",
    "Bulgarian": r"bulgarian|болгарск[\wё]*",
    "Catalan": r"catalan|каталонск[\wё]*",
    "Chinese": r"chinese|mandarin|cantonese|китайск[\wё]*|mandarin chinese",
    "Croatian": r"croatian|хорватск[\wё]*",
    "Czech": r"czech|чешск[\wё]*",
    "Danish": r"danish|датск[\wё]*",
    "Dutch": r"dutch|nederlands|нидерландск[\wё]*|голландск[\wё]*",
    "Estonian": r"estonian|эстонск[\wё]*",
    "Finnish": r"finnish|финск[\wё]*",
    "French": r"french|français|французск[\wё]*",
    "Georgian": r"georgian|грузинск[\wё]*",
    "German": r"german|deutsch(?:kenntnisse)?|немецк[\wё]*",
    "Greek": r"greek|греческ[\wё]*",
    "Hebrew": r"hebrew|иврит",
    "Hindi": r"hindi|хинди",
    "Hungarian": r"hungarian|венгерск[\wё]*",
    "Icelandic": r"icelandic|исландск[\wё]*",
    "Indonesian": r"indonesian|индонезийск[\wё]*",
    "Italian": r"italian|italiano|итальянск[\wё]*",
    "Japanese": r"japanese|японск[\wё]*",
    "Kazakh": r"kazakh|казахск[\wё]*",
    "Korean": r"korean|корейск[\wё]*",
    "Latvian": r"latvian|латышск[\wё]*",
    "Lithuanian": r"lithuanian|литовск[\wё]*",
    "Macedonian": r"macedonian|македонск[\wё]*",
    "Malay": r"malay|малайск[\wё]*",
    "Marathi": r"marathi|маратхи",
    "Norwegian": r"norwegian|норвежск[\wё]*",
    "Persian": r"persian|farsi|персидск[\wё]*|фарси",
    "Punjabi": r"punjabi|пенджабск[\wё]*",
    "Polish": r"polish|polski|польск[\wё]*",
    "Portuguese": r"portuguese|português|португальск[\wё]*",
    "Romanian": r"romanian|румынск[\wё]*",
    "Serbian": r"serbian|сербск[\wё]*",
    "Slovak": r"slovak|словацк[\wё]*",
    "Slovenian": r"slovenian|словенск[\wё]*",
    "Swedish": r"swedish|шведск[\wё]*",
    "Tagalog": r"tagalog|filipino|тагальск[\wё]*",
    "Telugu": r"telugu|телугу",
    "Thai": r"thai|тайск[\wё]*",
    "Turkish": r"turkish|türkçe|турецк[\wё]*",
    "Ukrainian": r"ukrainian|украинск[\wё]*",
    "Urdu": r"urdu|урду",
    "Uzbek": r"uzbek|узбекск[\wё]*",
    "Vietnamese": r"vietnamese|вьетнамск[\wё]*",
}
_GENERIC_LANGUAGE_NAME_RE = re.compile(
    r"(?<![\w-])(?P<name>[A-Za-zÀ-ÖØ-öø-ÿ][A-Za-zÀ-ÖØ-öø-ÿ'-]{1,30})\s+languages?\b",
    re.IGNORECASE,
)
_GENERIC_LANGUAGE_NON_NAMES = {
    "additional",
    "business",
    "computer",
    "foreign",
    "local",
    "markup",
    "modeling",
    "modelling",
    "native",
    "other",
    "programming",
    "query",
    "scripting",
    "second",
    "spoken",
    "technical",
    "written",
}
_REQUIRED_LANGUAGE_PREFIXES = (
    re.compile(
        r"(?:\b(?:command|knowledge|proficiency|fluency)\s+(?:of|in)|"
        r"\b(?:fluent|proficient|native)(?:\s+in)?|"
        r"\bflie(?:ß|ss)end|"
        r"\b(?:must|required\s+to|need\s+to)\s+(?:speak|read|write|know|use|communicate\s+in)|"
        r"\b(?:required|mandatory|essential)\s+(?:command|knowledge|proficiency|fluency)\s+(?:of|in))"
        r"\s+(?:the\s+)?[^.;:\n]{0,80}$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:\bсвободн[\wё]*\s+владен[\wё]*|\bзнани[\wё]*|\bвладени[\wё]*)"
        r"\s+[^.;:\n]{0,60}$",
        re.IGNORECASE,
    ),
    re.compile(r"\b[abc][12]\s+(?:level\s+)?$", re.IGNORECASE),
)
_REQUIRED_LANGUAGE_SUFFIXES = (
    re.compile(
        r"^\s*(?:language|language\s+skills|skills|\u044fзык[\wё]*|\u044fзыком)?\s*"
        r"(?:is|are|\u044fвляется)?\s*"
        r"(?:required|mandatory|essential|a\s+must|erforderlich|\u043eбязател[\wё]*|\u0442ребуется)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"^\s*(?:language\s+)?(?:proficiency|fluency|knowledge|command)?\s*"
        r"(?:at\s+)?(?:level\s+)?[abc][12]\b",
        re.IGNORECASE,
    ),
)


def detect_language(text: str) -> str:
    """Identify supported descriptions conservatively, blocking foreign/uncertain content.

    This is a local rule-based gate, not a statistical language model. Unsupported scripts,
    conflicting language evidence and insufficient positive evidence fail closed.
    """
    text = re.sub(r"https?://\S+|\b\S+@\S+\b", " ", text).casefold()
    words = re.findall(r"[^\W\d_]+", text)
    if not words:
        return "unknown"
    # Cyrillic is not synonymous with Russian; Latin is not synonymous with English.
    foreign_letters = [
        char
        for char in text
        if char.isalpha() and not ("LATIN" in unicodedata.name(char, "") or "а" <= char <= "я" or char == "ё")
    ]
    if len(foreign_letters) >= 3:
        return "unknown"
    folded = [
        "".join(
            char for char in unicodedata.normalize("NFKD", word.replace("ł", "l")) if not unicodedata.combining(char)
        )
        if any("LATIN" in unicodedata.name(char, "") for char in word)
        else word.replace("ё", "е")
        for word in words
    ]
    counts = Counter(folded)
    vocabulary = set(counts)
    other_scores = {code: len(vocabulary & markers) for code, markers in _OTHER_WORDS.items()}
    other_scores["pl"] = len(
        (vocabulary & _OTHER_WORDS["pl"]) | {word for word in vocabulary if _POLISH_STEMS.fullmatch(word)}
    )
    other, score = max(other_scores.items(), key=lambda item: item[1])
    if score >= 2:
        return other
    scores = {code: sum(counts[word] for word in vocabulary & markers) for code, markers in _ACCEPTED_WORDS.items()}
    language = max(scores, key=scores.get)
    recognized = set().union(*(_ACCEPTED_WORDS[code] & vocabulary for code in _ACCEPTED_WORDS))
    coverage = sum(counts[word] for word in recognized) / len(folded)
    if len(vocabulary & _ACCEPTED_WORDS[language]) < 2 or coverage < 0.15:
        return "unknown"
    return language


def detect_linkedin_content_language(text: str) -> str:
    """Compatibility entry point; all sources now use the same description policy."""
    return detect_language(text)


def explicit_unsupported_language_requirements(text: str) -> list[str]:
    """Return explicitly required languages outside the Russian/English/Spanish allowlist."""
    required: list[str] = []
    for language, language_pattern in LANGUAGE_NAME_PATTERNS.items():
        if language in ALLOWED_REQUIRED_LANGUAGES:
            continue
        for match in re.finditer(rf"(?<![\w-])(?:{language_pattern})(?![\w-])", text, re.IGNORECASE):
            if _is_explicit_language_requirement(text, match.start(), match.end()):
                required.append(language)
                break
    for match in _GENERIC_LANGUAGE_NAME_RE.finditer(text):
        candidate = match.group("name")
        if candidate.casefold() in _GENERIC_LANGUAGE_NON_NAMES:
            continue
        canonical = _canonical_language_name(candidate)
        if canonical in ALLOWED_REQUIRED_LANGUAGES or canonical in required:
            continue
        if _is_explicit_language_requirement(text, match.start(), match.end()):
            required.append(canonical)
    return required


def _canonical_language_name(name: str) -> str:
    for language, language_pattern in LANGUAGE_NAME_PATTERNS.items():
        if re.fullmatch(rf"(?:{language_pattern})", name, re.IGNORECASE):
            return language
    return name.capitalize()


def _is_explicit_language_requirement(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 120) : start]
    after = text[end : end + 80]
    return any(pattern.search(before) for pattern in _REQUIRED_LANGUAGE_PREFIXES) or any(
        pattern.search(after) for pattern in _REQUIRED_LANGUAGE_SUFFIXES
    )


def has_explicit_german_requirement(text: str) -> bool:
    """Backward-compatible predicate for callers that only need the German case."""
    return "German" in explicit_unsupported_language_requirements(text)
