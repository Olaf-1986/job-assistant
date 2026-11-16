# Корнеркейсы вакансий — C01–C20

Вымышленные примеры для приёмки. Это описательная спецификация, не отчёт о прохождении автотестов. Каждый кейс независим; история показов/откликов пуста. Варианты меняют только указанные поля.

[Общие критерии и сценарии A01–A34](criteria-review-2026-11-07.md) · [JSON](fixtures/criteria-corner-cases.json)

## Оглавление

- [C01 — Английское описание, обязательный немецкий](#c01)
- [C02 — Необязательный язык и рынок клиентов](#c02)
- [C03 — Обязательный высокий испанский](#c03)
- [C04 — Желательный высокий испанский](#c04)
- [C05 — Достаточно английского ИЛИ испанского](#c05)
- [C06 — Нужны английский И испанский](#c06)
- [C07 — Испанский intermediate](#c07)
- [C08 — Язык заголовка не заменяет язык описания](#c08)
- [C09 — Неоднозначное Remote Europe](#c09)
- [C10 — Часовой пояс вместо юридического ограничения](#c10)
- [C11 — Явное ограничение удалёнки](#c11)
- [C12 — Гибрид и реальное место офиса](#c12)
- [C13 — Дубликат из HH и Telegram с tracking-параметрами](#c13)
- [C14 — Дубликат не должен скрывать обязательное ограничение](#c14)
- [C15 — Два найма с одинаковыми компанией, ролью и городом](#c15)
- [C16 — Неуказанная налоговая база означает net](#c16)
- [C17 — Границы зарплатных порогов net](#c17)
- [C18 — Gross: пользовательская оценка net](#c18)
- [C19 — Свежесть условий по datetime публикации, а не длине текста](#c19)
- [C20 — Remote в техническом контексте или с отрицанием](#c20)

## Общие значения

- **Роль:** Business Analyst
- **Описание:** We are looking for a business analyst with experience in requirements analysis, stakeholder management and process documentation. You will work with our team on user stories and acceptance criteria.
- **Формат:** remote
- **География:** Remote worldwide, including Georgia
- **Зарплата:** {"min": 210000, "max": 260000, "currency": "RUB", "period": "month", "basis": "net"}
- **Опубликовано:** 2026-11-06T09:00:00Z
- **Получено:** 2026-11-07T08:00:00Z

<a id="c01"></a>
## C01 — Английское описание, обязательный немецкий

**Ожидание:** German обязателен и находится вне ru/en/es.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c01-a

- **Источник:** headhunter
- **ID источника:** synthetic-c01
- **Компания:** Synthetic Language One
- **Требования:** English B2 and fluent German are required.


<a id="c02"></a>
## C02 — Необязательный язык и рынок клиентов

**Ожидание:** Упоминание немецких клиентов и optional German не создают обязательного языка.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c02-a

- **Источник:** linkedin
- **ID источника:** synthetic-c02
- **Компания:** Synthetic Language Two
- **Требования:** English is required. German is a nice-to-have, not required. You will work with German clients in English.


<a id="c03"></a>
## C03 — Обязательный высокий испанский

**Ожидание:** Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c03-a

- **Источник:** telegram
- **ID источника:** synthetic-c03:1:0
- **Компания:** Synthetic Language Three
- **Требования:** Spanish C1 is mandatory for daily customer workshops.

- **Вариант c03-c2:** {"requirements": "Required: Spanish C2."}. Ожидание: Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.
- **Вариант c03-fluent:** {"requirements": "Fluent in Spanish, both written and spoken, is required."}. Ожидание: Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.
- **Вариант c03-native:** {"requirements": "Native Spanish speaker required."}. Ожидание: Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.
- **Вариант c03-ru:** {"requirements": "Обязателен испанский язык на уровне C1; свободное владение для интервью."}. Ожидание: Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.
- **Вариант c03-es:** {"requirements": "Se requiere español C1 para las reuniones con clientes."}. Ожидание: Обязательный Spanish C1/C2/fluent/native выше подтверждённого уровня пользователя.

<a id="c04"></a>
## C04 — Желательный высокий испанский

**Ожидание:** Высокий испанский явно optional; обязательный английский доступен.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c04-a

- **Источник:** linkedin
- **ID источника:** synthetic-c04
- **Компания:** Synthetic Language Four
- **Требования:** English C1 is required. Spanish C1 is preferred but not required. Fluent Spanish would be a bonus.


<a id="c05"></a>
## C05 — Достаточно английского ИЛИ испанского

**Ожидание:** Испанский C1 не обязателен при доступной английской альтернативе.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c05-a

- **Источник:** headhunter
- **ID источника:** synthetic-c05
- **Компания:** Synthetic Language Five
- **Требования:** English C1 OR Spanish C1 is required; either language alone is sufficient.


<a id="c06"></a>
## C06 — Нужны английский И испанский

**Ожидание:** Доступный английский не отменяет обязательного испанского C1.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c06-a

- **Источник:** telegram
- **ID источника:** synthetic-c06:1:0
- **Компания:** Synthetic Language Six
- **Требования:** English C1 AND Spanish C1 are required; both languages are used daily.


<a id="c07"></a>
## C07 — Испанский intermediate

**Ожидание:** Описание es разрешено; B1 не относится к подтверждённым блокерам C1/C2/fluent/native.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c07-a

- **Источник:** linkedin
- **ID источника:** synthetic-c07
- **Компания:** Synthetic Language Seven
- **Описание:** Buscamos experiencia en análisis de requisitos y documentación de procesos de negocio. El trabajo consiste en colaborar con el equipo y preparar historias de usuario.
- **Требования:** Se requiere español B1. English is available for technical documentation.


<a id="c08"></a>
## C08 — Язык заголовка не заменяет язык описания

**Ожидание:** Польское описание не разрешено, даже если заголовок и метаданные английские.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c08-a

- **Источник:** linkedin
- **ID источника:** synthetic-c08
- **Компания:** Synthetic Language Eight
- **declared_language:** en
- **Описание:** Business Analyst. Poszukujemy analityka do zespołu. Zakres obowiązków obejmuje współpracę z klientami oraz analizę wymagań. Oferujemy pracę zdalną. Wymagamy znajomości systemów i tworzenia dokumentacji.

- **Вариант c08-unknown:** {"description": "SQL BPMN REST qwerty zxcvb"}. Ожидание: Язык полного описания нельзя установить по нескольким техническим словам.

<a id="c09"></a>
## C09 — Неоднозначное Remote Europe

**Ожидание:** Неясная география remote больше не самостоятельный блокер.
**Результат:** shortlist_with_warning; карточек в shortlist: 1.

### Карточка c09-a

- **Источник:** linkedin
- **ID источника:** synthetic-c09
- **Компания:** Synthetic Remote Nine
- **География:** Remote, Europe
- **Требования:** Work remotely with our European team. Hiring countries are not specified.


<a id="c10"></a>
## C10 — Часовой пояс вместо юридического ограничения

**Ожидание:** Грузия явно разрешена; диапазон часов не является ограничением резидентства ЕС.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c10-a

- **Источник:** telegram
- **ID источника:** synthetic-c10:1:0
- **Компания:** Synthetic Remote Ten
- **География:** Remote worldwide, including Georgia. Four hours of overlap with UTC+2 to UTC+4 are required.
- **Требования:** English is required. Applicants may work from any country.


<a id="c11"></a>
## C11 — Явное ограничение удалёнки

**Ожидание:** Есть явное географическое ограничение, несовместимое с работой из Грузии.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c11-a

- **Источник:** linkedin
- **ID источника:** synthetic-c11
- **Компания:** Synthetic Remote Eleven
- **География:** Remote within the EU only
- **Требования:** Applicants must reside in an EU member state and have the right to work there. Georgia is not supported.

- **Вариант c11-russia:** {"location_text": "Удалённо только из РФ", "requirements": "Работа возможна только с территории России, из других стран работать нельзя."}. Ожидание: Есть явное географическое ограничение, несовместимое с работой из Грузии.

<a id="c12"></a>
## C12 — Гибрид и реальное место офиса

**Ожидание:** Обязательный офис в разрешённом городе Тбилиси.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c12-a

- **Источник:** headhunter
- **ID источника:** synthetic-c12
- **Компания:** Synthetic Office Twelve
- **Формат:** hybrid
- **География:** Tbilisi, Georgia
- **Требования:** Two days a week in our Tbilisi office. English is required.

- **Вариант c12-batumi:** {"location_text": "Batumi, Georgia", "requirements": "Three days a week in our Batumi office. The headquarters is in Tbilisi."}. Ожидание: Рабочий офис в Батуми; упоминание штаб-квартиры в Тбилиси не разрешает гибрид.

<a id="c13"></a>
## C13 — Дубликат из HH и Telegram с tracking-параметрами

**Ожидание:** Application URL обозначает один requisition; tracking не создаёт новый найм.
**Результат:** merged_shortlist; карточек в shortlist: 1.

### Карточка c13-a

- **Источник:** headhunter
- **ID источника:** synthetic-c13-hh
- **Компания:** Synthetic Duplicate Thirteen
- **Источник URL:** https://hh.example.invalid/vacancy/c13
- **Отклик:** https://careers.example.invalid/jobs/REQ-C13?utm_source=hh

### Карточка c13-b

- **Источник:** telegram
- **ID источника:** synthetic-c13:22:0
- **Компания:** Synthetic Duplicate Thirteen
- **Источник URL:** https://telegram.example.invalid/c13/22
- **Отклик:** https://careers.example.invalid/jobs/REQ-C13?utm_source=telegram&utm_campaign=jobs


<a id="c14"></a>
## C14 — Дубликат не должен скрывать обязательное ограничение

**Ожидание:** Требование Spanish C1 сохраняется после объединения при любом порядке. Подтверждения отмены условия нет.
**Результат:** merged_blocked; карточек в shortlist: 0.

### Карточка c14-a

- **Источник:** linkedin
- **ID источника:** synthetic-c14-li
- **Компания:** Synthetic Conflict Fourteen
- **Отклик:** https://careers.example.invalid/jobs/REQ-C14
- **Требования:** English C1 is required.

### Карточка c14-b

- **Источник:** telegram
- **ID источника:** synthetic-c14:23:0
- **Компания:** Synthetic Conflict Fourteen
- **Отклик:** https://careers.example.invalid/jobs/REQ-C14
- **Требования:** English C1 and Spanish C1 are both mandatory for this opening.

- **Вариант c14-geography:** {"location_text": "Remote for EU residents only", "requirements": "English C1 is required. Applicants must reside in an EU member state. Work from Georgia is not permitted."}. Ожидание: Явное EU-only из второго источника нельзя потерять за исходным remote worldwide.

<a id="c15"></a>
## C15 — Два найма с одинаковыми компанией, ролью и городом

**Ожидание:** Одинаковые компания, заголовок и город объединяют карточки, даже при разных requisition ID и описаниях. Пользователь согласился пока оставить это поведение.
**Результат:** merged_shortlist; карточек в shortlist: 1.

**Вариант с разными названиями:** `Business Analyst (Payments)` и `Business Analyst (Warehouse)` сохраняют текст в скобках и различаются; при отсутствии иных общих ключей ожидаются две карточки.

### Карточка c15-a

- **Источник:** headhunter
- **ID источника:** synthetic-c15-payments
- **Компания:** Synthetic Separate Fifteen
- **Формат:** hybrid
- **География:** Tbilisi
- **Requisition:** REQ-PAYMENTS-01
- **Отклик:** https://careers.example.invalid/jobs/REQ-PAYMENTS-01
- **Описание:** Business Analyst for our payments team. Analyze card processing requirements and document transaction reconciliation rules. Work with banking stakeholders on settlement acceptance criteria. This is opening REQ-PAYMENTS-01.

### Карточка c15-b

- **Источник:** headhunter
- **ID источника:** synthetic-c15-warehouse
- **Компания:** Synthetic Separate Fifteen
- **Формат:** hybrid
- **География:** Tbilisi
- **Requisition:** REQ-WAREHOUSE-02
- **Отклик:** https://careers.example.invalid/jobs/REQ-WAREHOUSE-02
- **Описание:** Business Analyst for our warehouse team. Gather inventory requirements and model stock movement processes. Prepare picking workflows with logistics stakeholders and define shipment specifications. This is separate opening REQ-WAREHOUSE-02.


<a id="c16"></a>
## C16 — Неуказанная налоговая база означает net

**Ожидание:** Пользователь явно указал считать неуточнённую сумму net; максимум выше 200000.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c16-a

- **Источник:** headhunter
- **ID источника:** synthetic-c16
- **Компания:** Synthetic Salary Sixteen
- **Зарплата:** {"min": 210000, "max": 250000, "currency": "RUB", "period": "month", "basis": null}


<a id="c17"></a>
## C17 — Границы зарплатных порогов net

**Ожидание:** Максимум ровно 200000 net включён в блокирующую границу.
**Результат:** blocked; карточек в shortlist: 0.

### Карточка c17-a

- **Источник:** headhunter
- **ID источника:** synthetic-c17
- **Компания:** Synthetic Salary Seventeen
- **Зарплата:** {"min": 170000, "max": 200000, "currency": "RUB", "period": "month", "basis": "net"}

- **Вариант c17-max-above:** {"salary": {"min": 170000, "max": 200001, "currency": "RUB", "period": "month", "basis": "net"}}. Ожидание: Максимум строго выше 200000.
- **Вариант c17-open-low:** {"salary": {"min": 149999, "max": null, "currency": "RUB", "period": "month", "basis": "net"}}. Ожидание: Минимум без максимума ниже 150000.
- **Вариант c17-open-boundary:** {"salary": {"min": 150000, "max": null, "currency": "RUB", "period": "month", "basis": "net"}}. Ожидание: Минимум без максимума ровно 150000 разрешён.
- **Вариант c17-annual:** {"salary": {"min": 2040000, "max": 2400000, "currency": "RUB", "period": "year", "basis": "net"}}. Ожидание: 2400000 в год равно 200000 в месяц.

<a id="c18"></a>
## C18 — Gross: пользовательская оценка net

**Ожидание:** RUB без явной страны найма означает российский рынок: gross / (1 + 13/87). Исходная сумма и допущение видны в карточке; показанная оценка округлена до копеек.
**Результат:** shortlist; карточек в shortlist: 1.

### Карточка c18-a

- **Источник:** headhunter
- **ID источника:** synthetic-c18
- **Компания:** Synthetic Salary Eighteen
- **Зарплата:** {"min": 300000, "max": 400000, "currency": "RUB", "period": "month", "basis": "gross", "source_gross_flag": true}

- **Вариант c18-ru-below:** {"salary": {"min": null, "max": 220000, "currency": "RUB", "period": "month", "basis": "gross"}}. Ожидание: 220000 / (1 + 13/87) = 191400 net, ниже порога.
- **Вариант c18-ru-above:** {"salary": {"min": null, "max": 230000, "currency": "RUR", "period": "month", "basis": "gross"}}. Ожидание: 230000 / (1 + 13/87) = 200100 net, выше порога.
- **Вариант c18-explicit-ge:** {"hiring_country": "Georgia", "salary": {"min": null, "max": 220000, "currency": "RUB", "period": "month", "basis": "gross"}}. Ожидание: Явная страна найма важнее RUB: для Грузии пользователь разрешил пренебречь 1%.
- **Вариант c18-explicit-ru-usd:** {"hiring_country": "Russia", "salary": {"min": null, "max": 2500, "currency": "USD", "period": "month", "basis": "gross"}}. Ожидание: Явная Россия важнее USD: 2500 / (1 + 13/87) × 87 = 189225 RUB net.

<a id="c19"></a>
## C19 — Свежесть условий по datetime публикации, а не длине текста

**Ожидание:** Новая полная версия опубликована на минуту позднее. Старое Spanish C1 не добавляется в текущие условия. Идентификаторы обоих источников сохраняются.
**Результат:** merged_shortlist; карточек в shortlist: 1.

### Карточка c19-old

- **Источник:** headhunter
- **ID источника:** synthetic-c19
- **Компания:** Synthetic Versions Nineteen
- **Отклик:** https://careers.example.invalid/jobs/REQ-C19
- **Опубликовано:** 2026-11-06T09:00:00Z
- **Требования:** English and Spanish C1 are mandatory.
- **Описание:** We are hiring a business analyst to document requirements and acceptance criteria. Work on process models with stakeholders and support integration specifications. Our projects involve customer workshops and detailed process documentation.
- **Полнота:** complete

### Карточка c19-new

- **Источник:** linkedin
- **ID источника:** synthetic-c19-li
- **Компания:** Synthetic Versions Nineteen
- **Отклик:** https://careers.example.invalid/jobs/REQ-C19
- **Опубликовано:** 2026-11-06T13:01:00+04:00
- **Требования:** English B2 is required.
- **Описание:** We need a business analyst to analyze requirements, document processes and prepare acceptance criteria with stakeholders.
- **Полнота:** complete

- **Вариант c19-equal:** {"published_at": "2026-11-06T13:00:00+04:00"}. Ожидание: Одинаковое время: конфликт не разрешён; сохраняется Spanish C1 и warning.
- **Вариант c19-no-date:** {"published_at": null, "received_at": "2026-11-09T09:00:00Z"}. Ожидание: Время получения письма не заменяет публикацию; сохраняется ограничение и warning.
- **Вариант c19-partial:** {"description_completeness": "incomplete"}. Ожидание: Неполная карточка не доказывает отмену старого условия.

<a id="c20"></a>
## C20 — Remote в техническом контексте или с отрицанием

**Ожидание:** Remote access и отрицание не доказывают удалённый формат. Географический warning не разрешает неизвестный формат с ручной проверкой.
**Результат:** manual_review; карточек в shortlist: 0.

### Карточка c20-a

- **Источник:** manual
- **ID источника:** synthetic-c20
- **Компания:** Synthetic Context Twenty
- **Формат:** unknown
- **География:** не указано
- **Требования:** Experience with remote access and distributed systems. This role is not remote.
- **requires_manual_location_review:** True
