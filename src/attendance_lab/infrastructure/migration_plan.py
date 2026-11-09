"""Local approval form and deterministic migration of explicitly approved legacy lessons."""

import hashlib
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

from attendance_lab.application.redesign import SLOTS
from attendance_lab.domain.models import AttendanceError


def write_approval_form(output: Path, report: dict) -> None:
    import copy
    import sqlite3
    from contextlib import closing

    proposed = copy.deepcopy(report)
    snapshot = output / "original.sqlite3"
    if snapshot.is_file():
        with closing(sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            if source.execute(
                "SELECT 1 FROM sqlite_master WHERE name='annual_memberships'"
            ).fetchone():
                for course in proposed["courses"]:
                    course["known_study_courses"] = [
                        r[0]
                        for r in source.execute(
                            "SELECT DISTINCT c.number FROM annual_memberships a "
                            "JOIN academic_groups g ON g.id=a.group_id JOIN courses c ON c.id=g.course_id "
                            "WHERE a.student_id IN (SELECT student_id FROM discipline_roster WHERE discipline_id=? "
                            "UNION SELECT rs.student_id FROM roster_students rs JOIN teaching_rosters tr "
                            "ON tr.id=rs.roster_id WHERE tr.discipline_id=?) ORDER BY c.number",
                            (course["discipline_id"], course["discipline_id"]),
                        )
                    ]
    proposal_path = output / "form-proposals.json"
    if proposal_path.is_file():
        overrides = json.loads(proposal_path.read_text(encoding="utf-8"))
        rows = {str(row["lesson_id"]): row for row in proposed["legacy_plan"]}
        for key, action in overrides.get("suggested_actions", {}).items():
            if key not in rows or action not in ("migrate", "delete"):
                raise AttendanceError("Локальное предложение относится к неизвестному занятию.")
            rows[key]["suggested_action"] = action
        course_defaults = overrides.get("course_defaults", {})
        allowed_course_fields = {
            "name",
            "academic_year_start",
            "semester",
            "study_course",
            "kind",
            "duration",
        }
        course_map = {course["key"]: course for course in proposed["courses"]}
        for key, defaults in course_defaults.items():
            if (
                key not in course_map
                or not isinstance(defaults, dict)
                or not set(defaults) <= allowed_course_fields
            ):
                raise AttendanceError("Локальные предложения курса содержат неизвестные поля.")
            course_map[key].update(defaults)
        additions = overrides.get("additional_lessons", [])
        if not isinstance(additions, list):
            raise AttendanceError("Локальные добавления должны быть отдельным списком.")
        allowed = {
            "key",
            "roster_source_lesson_id",
            "local_day",
            "date_confirmed",
            "topic_number",
            "topic_title",
            "start_slot",
            "course_key",
            "source_relative_topic_offset",
            "renumber_after_source",
        }
        for addition in additions:
            if (
                not isinstance(addition, dict)
                or not set(addition) <= allowed
                or str(addition.get("roster_source_lesson_id")) not in rows
            ):
                raise AttendanceError(
                    "Проверьте локальное предложение нового занятия и источник состава."
                )
        proposed["additional_lessons"] = copy.deepcopy(additions)
        default_source = overrides.get("default_roster_source_lesson_id")
        if default_source is not None:
            if str(default_source) not in rows:
                raise AttendanceError("Источник состава по умолчанию отсутствует в плане.")
            proposed["default_roster_source_lesson_id"] = default_source
    payload = json.dumps(proposed, ensure_ascii=False).replace("<", "\\u003c")
    document = r"""<!doctype html><html lang="ru"><meta charset="utf-8"><title>Проверка переноса Attendance</title>
<style>body{font:16px system-ui;margin:24px;color:#17304b;background:#f7f9fc}h1{margin-bottom:8px}h2{margin-top:32px}.notice{background:#fff4cf;padding:16px;border:1px solid #e2ba4f;border-radius:10px;line-height:1.5}.course{display:flex;flex-wrap:wrap;gap:12px;background:white;padding:18px;margin:14px 0;border:1px solid #ccd6df;border-radius:10px}.course h3{width:100%;margin:0}label{display:grid;gap:5px}small{display:block;color:#506277;font-size:12px;max-width:240px;font-weight:normal}input,select{font:inherit;padding:7px;border:1px solid #9aaabd;border-radius:5px;max-width:240px}input[type=number]{width:75px}input[type=checkbox]{width:20px;height:20px}table{border-collapse:collapse;width:100%;font-size:14px;background:white}td,th{border:1px solid #ccd6df;padding:9px;text-align:left;vertical-align:top}th{background:#eaf0f7}td select,td input{max-width:210px;width:100%;box-sizing:border-box}button{font:inherit;padding:10px 16px;border:1px solid #173f72;border-radius:6px;background:#173f72;color:white;cursor:pointer;margin:8px 0}.table-wrap{overflow:auto;border-radius:8px}.actions{position:sticky;bottom:0;background:#f7f9fc;padding:16px 0;border-top:1px solid #ccd6df}.actions label{display:flex;align-items:center}.secondary{background:white;color:#173f72}.muted{font-size:14px;color:#506277}</style>
<h1>Проверьте предложения для переноса</h1>
<div class="notice"><strong>Предложение — проверьте.</strong> Поля уже заполнены для удобства. Год и семестр предложены по дате, формат — по названию дисциплины, номер темы — по порядку и названию занятий. Это не установленные академические факты.<br>Для времени выбран ближайший допустимый первый слот по началу старого занятия; при равном расстоянии — более ранний. Рядом показано «было → предложено». Для тестовых строк доступно «Удалить тестовое занятие с восстановлением»: запись скрывается, исходная история сохраняется. Удаление существующего блока охватывает обе пары. Рабочая база и исходный план не изменяются. Перенос возможен только после вашего подтверждения.</div>
<p>Проверьте курсы сверху, затем занятия. Если один старый предмет нужно разделить по семестрам или форматам, добавьте курс и назначьте его нужным занятиям.</p>
<h2>1. Семестровые курсы</h2><div id="courses"></div><label>Разделить исходную дисциплину<select id="splitSource"></select><small>Новый курс сначала получит предложения исходной дисциплины. Измените его семестр, формат и название.</small></label><button class="secondary" id="addCourse">Добавить семестровый курс</button>
<h2>2. Занятия по курсам</h2><div id="lessons"></div>
<h2>3. Добавить занятие, на котором ридер не работал</h2><p>Сначала выберите исторический состав. Новая строка не создаёт посещений. Предложенную дату нужно подтвердить отдельно.</p><label>Источник состава<select id="additionalSource"></select></label><button class="secondary" id="addAdditional">Добавить занятие без предъявлений</button><div id="additionalLessons"></div>
<div class="actions"><label><input id="approved" type="checkbox"> Я проверил предложения: курсы, семестры, темы, время и объединение пар</label><button id="save">Сохранить утверждённый план</button><span id="status" role="status"></span></div>
<script>const original=PAYLOAD;const plan=structuredClone(original);plan.approved=false;plan.additional_lessons??=[];
const slots=[['09:00','10:20'],['10:30','11:50'],['12:00','13:20'],['13:50','15:10'],['15:20','16:40'],['17:00','18:20'],['18:30','19:50'],['20:00','21:20']];
const kinds=[['lecture','Лекции'],['lab','Лабораторные'],['seminar','Семинары'],['other','Другое']];
function node(tag,text){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;return n}
function select(options,value,onchange){const n=node('select');for(const [v,label] of options)n.append(new Option(label,String(v)));n.value=String(value??'');n.onchange=()=>onchange(n.value);return n}
function input(type,value,onchange){const n=node('input');n.type=type;n.value=value??'';if(type==='number'){n.min='1';n.step='1'}n.onchange=()=>onchange(type==='number'?Number(n.value):n.value);return n}
function label(text,control,help){const n=node('label',text);n.append(control);if(help)n.append(node('small',help));return n}
function courseName(c){return c.name+' · '+c.academic_year_start+'/'+String(c.academic_year_start+1).slice(-2)+' · '+(c.semester===1?'осень':'весна')+' · '+(kinds.find(x=>x[0]===c.kind)?.[1]??'')}
function minutes(t){const [h,m]=t.split(':').map(Number);return h*60+m}
function nearest(row,duration){const old=row.old_local_start.slice(11,16);let best=1,distance=Infinity;for(let i=1;i<=9-duration;i++){const d=Math.abs(minutes(old)-minutes(slots[i-1][0]));if(d<distance){distance=d;best=i}}return best}
function defaultCourse(c){const rows=plan.legacy_plan.filter(r=>r.discipline_id===c.discipline_id).sort((a,b)=>a.old_starts_at.localeCompare(b.old_starts_at));const dates=rows.map(r=>r.local_day);const first=dates[0]??new Date().toISOString().slice(0,10);const month=Number(first.slice(5,7));const year=Number(first.slice(0,4));const name=c.legacy_discipline.toLocaleLowerCase();const javaLab=rows.some(r=>r.required_duration===2)||(/java/.test(name)&&/лаб|lab/.test(name));c.academic_year_start??=month>=8?year:year-1;c.semester??=month>=8?1:2;c.study_course??=c.known_study_courses?.length===1?c.known_study_courses[0]:2;c.kind??=javaLab||/лаб|lab/.test(name)?'lab':/лек|lect/.test(name)?'lecture':/семинар|seminar/.test(name)?'seminar':'other';c.duration??=javaLab||c.kind==='lab'?2:1;c.name=c.name?.trim()||c.legacy_discipline;c.proposal_note=(c.known_study_courses?.length===1?'Курс обучения найден в прежнем составе.':'Курс обучения 2 — предложение, проверьте: однозначного состава нет.')+' Год и семестр — по первой дате '+first+'.'}
for(const c of plan.courses)defaultCourse(c);
for(const row of plan.legacy_plan){row.action??=row.suggested_action??'migrate';row.course_key??=plan.courses.find(c=>c.discipline_id===row.discipline_id).key;const c=plan.courses.find(c=>c.key===row.course_key);row.start_slot??=nearest(row,c.duration);if(row.start_slot>9-c.duration)row.start_slot=nearest(row,c.duration);row.merge_into??=null;}
for(const c of plan.courses){const rows=plan.legacy_plan.filter(r=>r.course_key===c.key).sort((a,b)=>a.old_starts_at.localeCompare(b.old_starts_at)||a.lesson_id-b.lesson_id);let next=1;const titles=new Map();for(const row of rows){if(row.action==='delete'||row.merge_into!==null)continue;let title=row.topic_title?.trim();if(title&&titles.has(title)){row.topic_number??=titles.get(title)}else{row.topic_number??=next++;if(title)titles.set(title,row.topic_number)}row.topic_title=title||'Занятие №'+row.topic_number;}for(const row of rows){if(row.action!=='delete'&&row.merge_into!==null){const first=rows.find(r=>r.lesson_id===row.merge_into);if(first){row.topic_number=first.topic_number;row.topic_title=first.topic_title;row.start_slot=first.start_slot}}}}
function refresh(){renderCourses();renderLessons();renderSplit();renderAdditional()}
function renderCourses(){const root=document.getElementById('courses');root.replaceChildren();for(const c of plan.courses){const box=node('section');box.className='course';box.append(node('h3',courseName(c)));const years=[...new Set([c.academic_year_start-1,c.academic_year_start,c.academic_year_start+1,c.academic_year_start+2])].sort();const yearSelect=select(years.map(y=>[y,y+'/'+String(y+1).slice(-2)]).concat([['other','Другой учебный год…']]),c.academic_year_start,v=>{if(v==='other'){const n=Number(prompt('Год начала учебного года',c.academic_year_start));if(Number.isInteger(n)&&n>=1900&&n<=2200)c.academic_year_start=n}else c.academic_year_start=Number(v);refresh()});box.append(label('Название курса',input('text',c.name,v=>{c.name=v;renderLessons();renderSplit()}),'Название можно уточнить для семестра и формата.'),label('Учебный год',yearSelect,'Предложен по первой дате: август–декабрь — год начала, январь–июль — предыдущий год.'),label('Семестр',select([[1,'Осень · 1-й семестр'],[2,'Весна · 2-й семестр']],c.semester,v=>{c.semester=Number(v);refresh()}),'Предложен по первой дате. Измените, если курс относится к другому семестру.'));const studyChoices=[...new Set([1,2,3,4,5,6,...(c.known_study_courses??[]),c.study_course])].sort((a,b)=>a-b).map(v=>[v,v+'-й курс']);studyChoices.push(['other','Другой курс…']);box.append(label('Курс обучения',select(studyChoices,c.study_course,v=>{if(v==='other'){const n=Number(prompt('Положительный номер курса обучения',c.study_course));if(Number.isInteger(n)&&n>0)c.study_course=n}else c.study_course=Number(v);refresh()}),c.proposal_note),label('Формат занятий',select(kinds,c.kind,v=>{c.kind=v;if(v==='lab')c.duration=2;refresh()}),'Предложен по названию исходной дисциплины. Java-лабораторные — две пары.'),label('Длительность',select([[1,'Одна пара'],[2,'Две последовательные пары']],c.duration,v=>{c.duration=Number(v);for(const r of plan.legacy_plan.filter(r=>r.course_key===c.key))if(r.start_slot>9-c.duration)r.start_slot=nearest(r,c.duration);refresh()}),'Одна регистрация закрывает весь блок.'));root.append(box)}}
function renderSplit(){const n=document.getElementById('splitSource');const keep=n.value;n.replaceChildren();for(const c of plan.courses)n.append(new Option(courseName(c),c.key));if([...n.options].some(x=>x.value===keep))n.value=keep}
function mergeChoices(row){const c=plan.courses.find(c=>c.key===row.course_key);const options=[['','Отдельное занятие']];if(c?.duration!==2)return options;for(const r of plan.legacy_plan){if(r.action==='delete'||r.lesson_id===row.lesson_id||r.course_key!==row.course_key||r.local_day!==row.local_day||r.topic_number!==row.topic_number||r.topic_title!==row.topic_title||r.merge_into!==null||r.timezone!==row.timezone||r.start_slot!==row.start_slot)continue;if(plan.legacy_plan.some(x=>x.merge_into===row.lesson_id))continue;options.push([r.lesson_id,r.local_day+' · тема '+r.topic_number+' · '+r.topic_title+' · ID '+r.lesson_id])}return options}
function renderLessons(){const root=document.getElementById('lessons');root.replaceChildren();for(const c of plan.courses){const rows=plan.legacy_plan.filter(r=>r.course_key===c.key).sort((a,b)=>a.old_starts_at.localeCompare(b.old_starts_at)||a.lesson_id-b.lesson_id);if(!rows.length)continue;root.append(node('h3',courseName(c)));const wrap=node('div');wrap.className='table-wrap';const table=node('table'),head=node('tr');for(const [title,help] of [['Исходное занятие','Старая дисциплина и локальное время; фактическое время карты не меняется.'],['Действие','Перенести в курс или удалить тестовую запись с возможностью восстановления. Для удаляемых строк учебные поля не требуются.'],['Семестровый курс','Выберите курс своей исходной дисциплины.'],['Тема программы','Один номер означает одну общую тему для курса. Предложение — по хронологии/названию.'],['Дата','Плановая дата блока. По умолчанию сохранена старая.'],['Время блока','Предложен ближайший допустимый первый слот по началу старого занятия. Проверьте изменение.'],['Объединение пар','Для общего двухпарного блока обеим строкам задайте одинаковые курс, дату, тему и первый слот.']]){let th=node('th',title);th.append(node('small',help));head.append(th)}table.append(head);for(const r of rows){const tr=node('tr');let old=node('td',r.legacy_discipline+' · ID '+r.lesson_id);old.append(node('small',r.old_local_start.slice(0,10)+' '+r.old_local_start.slice(11,16)+'–'+r.old_local_end.slice(11,16)));if(r.required_duration===2)old.append(node('small','Сохранить две пары'));tr.append(old);let actionCell=node('td');actionCell.append(select([['migrate','Перенести'],['delete','Удалить тестовое занятие с восстановлением']],r.action,v=>{const targets=r.existing_block_id==null?[r]:plan.legacy_plan.filter(x=>x.existing_block_id===r.existing_block_id);for(const target of targets){target.action=v;if(target.existing_block_id==null)target.merge_into=null}for(const x of plan.legacy_plan)if(targets.some(t=>t.lesson_id===x.merge_into)&&x.action!=='delete')x.merge_into=null;renderLessons()}));if(r.suggested_action==='delete')actionCell.append(node('small','Предложено удалить: точное совпадение тестовой записи, указанной вами. Проверьте.'));tr.append(actionCell);let cell=node('td');cell.append(select(plan.courses.filter(x=>x.discipline_id===r.discipline_id).map(x=>[x.key,courseName(x)]),r.course_key,v=>{r.course_key=v;r.merge_into=null;const target=plan.courses.find(x=>x.key===v);if(r.start_slot>9-target.duration)r.start_slot=nearest(r,target.duration);renderLessons()}));tr.append(cell);cell=node('td');cell.append(label('№ темы',input('number',r.topic_number,v=>{r.topic_number=v;renderLessons()})),label('Название темы',input('text',r.topic_title,v=>{r.topic_title=v;renderLessons()})));tr.append(cell);cell=node('td');cell.append(input('date',r.local_day,v=>{r.local_day=v;renderLessons()}));tr.append(cell);cell=node('td');const options=slots.slice(0,9-c.duration).map((s,i)=>[i+1,'Пара '+(i+1)+' · '+s[0]+'–'+slots[i+c.duration-1][1]+(c.duration===2?' · 2 пары':'')]);cell.append(select(options,r.start_slot,v=>{r.start_slot=Number(v);renderLessons()}),node('small','Было '+r.old_local_start.slice(11,16)+'–'+r.old_local_end.slice(11,16)+' → '+slots[r.start_slot-1][0]+'–'+slots[r.start_slot+c.duration-2][1]));tr.append(cell);cell=node('td');const optionsMerge=mergeChoices(r);if(r.merge_into!==null&&!optionsMerge.some(x=>String(x[0])===String(r.merge_into)))optionsMerge.push([r.merge_into,'Существующий блок · ID '+r.merge_into]);cell.append(select(optionsMerge,r.merge_into??'',v=>{r.merge_into=v===''?null:Number(v);renderLessons()}));if(optionsMerge.length===1&&c.duration===2)cell.append(node('small','Если две старые строки — один блок, сначала согласуйте их тему и время. Если второй старой пары нет, создаётся плановая пара без сканирований.'));tr.append(cell);if(r.action==='delete'){tr.style.background='#fff1f0';for(const control of tr.querySelectorAll('td:nth-child(n+3) input,td:nth-child(n+3) select'))control.disabled=true;}table.append(tr)}wrap.append(table);root.append(wrap)}}
function updateTopicNumber(row,value){const old=row.topic_number;if(row.topic_title==='Занятие №'+old)row.topic_title='Занятие №'+value;row.topic_number=value}
function shiftAdditional(item,enabled){if(Boolean(item.shift_applied)===enabled)return;const threshold=enabled?item.renumber_threshold:item.renumber_threshold+1;for(const r of [...plan.legacy_plan,...plan.additional_lessons]){if(r===item||!Number.isInteger(r.topic_number)||r.action==='delete'||r.course_key!==item.course_key||r.topic_number<=threshold)continue;updateTopicNumber(r,r.topic_number+(enabled?1:-1));if(r.shift_applied&&Number.isInteger(r.renumber_threshold))r.renumber_threshold+=enabled?1:-1;}item.shift_applied=enabled;item.renumber_after_source=enabled}
function renderAdditionSource(){const n=document.getElementById('additionalSource'),keep=n.value;n.replaceChildren();const rows=plan.legacy_plan.filter(r=>r.action!=='delete');for(const r of rows)n.append(new Option(r.local_day+' · '+r.legacy_discipline+' · тема '+r.topic_number+' · ID '+r.lesson_id,r.lesson_id));if(rows.some(r=>String(r.lesson_id)===keep))n.value=keep;else if(rows.some(r=>r.lesson_id===plan.default_roster_source_lesson_id))n.value=String(plan.default_roster_source_lesson_id)}
function renderAdditional(){renderAdditionSource();const root=document.getElementById('additionalLessons');root.replaceChildren();for(const r of plan.additional_lessons){const c=plan.courses.find(c=>c.key===r.course_key);const box=node('section');box.className='course';box.append(node('h3','Новое занятие без предъявлений · '+(r.local_day||'дата не выбрана')),node('small','Создаётся завершённое историческое занятие с фиксированным составом. Посещений, предъявлений и ручных отметок автоматически не будет.'));box.append(label('Семестровый курс',select(plan.courses.map(x=>[x.key,courseName(x)]),r.course_key,v=>{const shifted=r.shift_applied;if(shifted)shiftAdditional(r,false);r.course_key=v;const source=plan.legacy_plan.find(x=>x.action!=='delete'&&x.course_key===v);r.roster_source_lesson_id=source?.lesson_id??null;r.renumber_threshold=source?.topic_number??0;r.topic_number=r.renumber_threshold+1;r.topic_title='Занятие №'+r.topic_number;r.start_slot=source?.start_slot??1;if(shifted)shiftAdditional(r,true);renderLessons();renderAdditional()}),'Выберите курс, в котором действительно прошло отсутствующее занятие.'));const sources=plan.legacy_plan.filter(x=>x.action!=='delete'&&x.course_key===r.course_key);box.append(label('Состав из исторического занятия',select(sources.map(x=>[x.lesson_id,x.local_day+' · тема '+x.topic_number+' · ID '+x.lesson_id]),r.roster_source_lesson_id,v=>{const shift=r.shift_applied;if(shift)shiftAdditional(r,false);r.roster_source_lesson_id=Number(v);const source=sources.find(x=>x.lesson_id===Number(v));r.renumber_threshold=source.topic_number;r.topic_number=source.topic_number+1;r.topic_title='Занятие №'+r.topic_number;r.start_slot=source.start_slot;if(shift)shiftAdditional(r,true);renderLessons();renderAdditional()}),'Копируется только список студентов выбранного занятия; факты посещения не копируются.'),label('№ темы',input('number',r.topic_number,v=>{r.topic_number=v;renderAdditional()}),'Предложен следующий номер после источника состава. Проверьте общую программу.'),label('Название темы',input('text',r.topic_title,v=>{r.topic_title=v}),'Уточните тему. Пользовательские названия других тем сохраняются.'),label('Дата занятия',input('date',r.local_day,v=>{r.local_day=v;r.date_confirmed=true;renderAdditional()}),'Дата +7 дней от источника — только предложение. Подтвердите точный день.'));const dateCheck=node('input');dateCheck.type='checkbox';dateCheck.checked=r.date_confirmed===true;dateCheck.onchange=()=>{r.date_confirmed=dateCheck.checked};box.append(label('Точная дата подтверждена',dateCheck,'Пока дата не подтверждена, новая строка остаётся ожидающей и утверждение плана блокируется.'));const options=slots.slice(0,9-c.duration).map((s,i)=>[i+1,'Пара '+(i+1)+' · '+s[0]+'–'+slots[i+c.duration-1][1]+(c.duration===2?' · 2 пары':'')]);box.append(label('Время блока',select(options,r.start_slot,v=>r.start_slot=Number(v)),'Предложен слот выбранного источника; проверьте фактическое расписание.'));const shift=node('input');shift.type='checkbox';shift.checked=r.shift_applied===true;shift.onchange=()=>{shiftAdditional(r,shift.checked);renderLessons();renderAdditional()};box.append(label('Сдвинуть последующие предложенные номера',shift,'Меняются только предложения этой формы. Автоматические названия «Занятие №…» обновляются; введённые пользователем названия сохраняются.'));const remove=node('button','Убрать добавление');remove.className='secondary';remove.onclick=()=>{if(r.shift_applied)shiftAdditional(r,false);plan.additional_lessons=plan.additional_lessons.filter(x=>x!==r);renderLessons();renderAdditional()};box.append(remove);root.append(box)}}
document.getElementById('addAdditional').onclick=()=>{const source=plan.legacy_plan.find(r=>r.lesson_id===Number(document.getElementById('additionalSource').value));if(!source)return;const d=new Date(source.local_day+'T12:00:00Z');d.setUTCDate(d.getUTCDate()+7);const r={key:'new-'+Date.now(),course_key:source.course_key,roster_source_lesson_id:source.lesson_id,topic_number:source.topic_number+1,topic_title:'Занятие №'+(source.topic_number+1),local_day:d.toISOString().slice(0,10),date_confirmed:false,start_slot:source.start_slot,renumber_threshold:source.topic_number,renumber_after_source:true,shift_applied:false};plan.additional_lessons.push(r);shiftAdditional(r,true);renderLessons();renderAdditional()};

for(const r of plan.additional_lessons){const source=plan.legacy_plan.find(x=>x.lesson_id===r.roster_source_lesson_id);if(!source)continue;r.course_key??=source.course_key;r.start_slot??=source.start_slot;const offset=r.source_relative_topic_offset??1;r.renumber_threshold=source.topic_number+offset-1;r.topic_number??=source.topic_number+offset;r.topic_title??='Занятие №'+r.topic_number;r.shift_applied=false;r.renumber_after_source??=true;if(r.renumber_after_source)shiftAdditional(r,true);}
document.getElementById('addCourse').onclick=()=>{const source=plan.courses.find(c=>c.key===document.getElementById('splitSource').value);if(!source)return;plan.courses.push({...source,key:'course-'+Date.now(),name:source.name+' · отдельный семестр/формат'});refresh()};
document.getElementById('save').onclick=()=>{for(const c of plan.courses){if(![...plan.legacy_plan,...plan.additional_lessons].some(r=>r.action!=='delete'&&r.course_key===c.key))continue;if(!c.name?.trim()||!Number.isInteger(c.academic_year_start)||c.academic_year_start<1900||c.academic_year_start>2200||![1,2].includes(c.semester)||!Number.isInteger(c.study_course)||c.study_course<1||!c.kind||![1,2].includes(c.duration)){alert('Проверьте заполнение курса '+c.name);return}}for(const r of plan.legacy_plan){if(r.action==='delete')continue;const c=plan.courses.find(c=>c.key===r.course_key);if(!c||!Number.isInteger(r.topic_number)||r.topic_number<1||!r.topic_title?.trim()||!r.local_day||!Number.isInteger(r.start_slot)||r.start_slot<1||r.start_slot>9-c.duration||(r.required_duration===2&&c.duration!==2)){alert('Проверьте занятие '+r.lesson_id+': курс, тему, дату, слот и длительность.');return}const same=plan.legacy_plan.filter(x=>x.action!=='delete'&&x.course_key===r.course_key&&x.topic_number===r.topic_number);if(same.some(x=>x.topic_title.trim()!==r.topic_title.trim())){alert('Одинаковый номер темы должен иметь одинаковое название. Проверьте тему '+r.topic_number+' курса '+c.name);return}if(r.merge_into!==null){const first=plan.legacy_plan.find(x=>x.lesson_id===r.merge_into);if(!first||first.merge_into!==null||first.course_key!==r.course_key||first.topic_number!==r.topic_number||first.local_day!==r.local_day||first.start_slot!==r.start_slot||first.timezone!==r.timezone){alert('Проверьте объединение занятия '+r.lesson_id);return}}}for(const r of plan.additional_lessons){const c=plan.courses.find(c=>c.key===r.course_key);const source=plan.legacy_plan.find(x=>x.lesson_id===r.roster_source_lesson_id);if(!c||!source||source.action==='delete'||source.course_key!==r.course_key||!r.date_confirmed||!r.local_day||!Number.isInteger(r.topic_number)||r.topic_number<1||!r.topic_title?.trim()||!Number.isInteger(r.start_slot)||r.start_slot<1||r.start_slot>9-c.duration){alert('Проверьте новое занятие: курс, источник состава, тему, время и подтверждение точной даты.');return}}if(!document.getElementById('approved').checked){alert('Подтвердите проверку предложений флажком внизу.');return}plan.approved=true;const blob=new Blob([JSON.stringify(plan,null,2)],{type:'application/json'});const a=node('a');a.href=URL.createObjectURL(blob);a.download='approved-plan.json';a.click();URL.revokeObjectURL(a.href);document.getElementById('status').textContent='План сохранён. Рабочая база не изменена.'};refresh();</script></html>
""".replace("PAYLOAD", payload)
    path = output / "review-plan.html"
    path.write_text(document, encoding="utf-8")
    path.chmod(0o600)


def validate_decisions(report: dict, approved: dict) -> tuple[dict, dict]:
    if approved.get("approved") is not True or approved.get("tables") != report["tables"]:
        raise AttendanceError("План не утверждён или относится к другой копии базы.")
    originals = {r["lesson_id"]: r for r in report["legacy_plan"]}
    rows = {r["lesson_id"]: r for r in approved["legacy_plan"]}
    if len(rows) != len(approved["legacy_plan"]) or rows.keys() != originals.keys():
        raise AttendanceError("План должен содержать каждое исходное занятие ровно один раз.")
    courses = {c["key"]: c for c in approved["courses"]}
    if len(courses) != len(approved["courses"]):
        raise AttendanceError("Ключи курсов должны различаться.")
    used_keys = {
        r.get("course_key") for r in rows.values() if r.get("action", "migrate") == "migrate"
    }
    if not isinstance(approved.get("additional_lessons", []), list):
        raise AttendanceError("Новые занятия должны быть отдельным списком плана.")
    used_keys.update(r.get("course_key") for r in approved.get("additional_lessons", []))
    courses = {key: c for key, c in courses.items() if key in used_keys}
    for c in courses.values():
        if (
            type(c.get("academic_year_start")) is not int
            or not 1900 <= c["academic_year_start"] <= 2200
            or type(c.get("semester")) is not int
            or c["semester"] not in (1, 2)
            or type(c.get("study_course")) is not int
            or c["study_course"] <= 0
            or c.get("kind") not in ("lecture", "lab", "seminar", "other")
            or type(c.get("duration")) is not int
            or c["duration"] not in (1, 2)
            or not isinstance(c.get("name"), str)
            or not c["name"].strip()
        ):
            raise AttendanceError(
                "Заполните год, семестр, курс обучения, формат, длительность и название каждого курса."
            )
    for key, r in rows.items():
        old = originals[key]
        if r.get("action", "migrate") not in ("migrate", "delete"):
            raise AttendanceError("Выберите перенос или обратимое удаление занятия.")
        if r.get("action") == "delete":
            continue
        c = courses.get(r.get("course_key"))
        if c is None or c["discipline_id"] != old["discipline_id"]:
            raise AttendanceError("Занятие должно получить курс своей исходной дисциплины.")
        if (
            type(r.get("topic_number")) is not int
            or r["topic_number"] < 1
            or not isinstance(r.get("topic_title"), str)
            or not r["topic_title"].strip()
            or type(r.get("start_slot")) is not int
            or not 1 <= r["start_slot"] <= 9 - c["duration"]
        ):
            raise AttendanceError("Укажите положительный номер темы и доступный слот блока.")
        date.fromisoformat(r["local_day"])
        if old["required_duration"] == 2 and c["duration"] != 2:
            raise AttendanceError("Исторические Java-лабораторные должны содержать две пары.")
        if r.get("merge_into") is not None:
            primary = rows.get(r["merge_into"])
            if (
                primary is None
                or primary.get("action") == "delete"
                or primary is r
                or primary.get("merge_into") is not None
                or c["duration"] != 2
                or primary.get("course_key") != r["course_key"]
                or primary.get("topic_number") != r["topic_number"]
                or primary.get("local_day") != r["local_day"]
                or primary.get("start_slot") != r["start_slot"]
                or originals[primary["lesson_id"]]["timezone"] != old["timezone"]
            ):
                raise AttendanceError(
                    "Объединяемые строки должны иметь общий курс, тему, дату и первый слот блока."
                )
    for key, old in originals.items():
        if old.get("existing_block_id") is not None:
            block_actions = {
                rows[k].get("action", "migrate")
                for k, v in originals.items()
                if v.get("existing_block_id") == old["existing_block_id"]
            }
            if len(block_actions) != 1:
                raise AttendanceError("Существующий блок удаляется или переносится целиком.")
            if rows[key].get("action") == "delete":
                continue
            primary = rows[key].get("merge_into") or key
            expected = {
                k
                for k, v in originals.items()
                if v.get("existing_block_id") == old["existing_block_id"]
            }
            actual = {primary} | {k for k, v in rows.items() if v.get("merge_into") == primary}
            if actual != expected:
                raise AttendanceError(
                    "Существующий блок и его история регистрации должны сохраняться целиком."
                )
    for key in rows:
        if rows[key].get("action") == "delete":
            continue
        if sum(r.get("merge_into") == key for r in rows.values()) > 1:
            raise AttendanceError("Двухпарный блок объединяет не более двух старых занятий.")
    topics = {}
    for r in rows.values():
        if r.get("action") == "delete":
            continue
        key = (r["course_key"], r["topic_number"])
        title = r.get("topic_title", "").strip()
        if key in topics and topics[key] != title:
            raise AttendanceError("Один номер темы должен иметь одно название во всём курсе.")
        topics[key] = title
    return courses, rows


def validate_additional_lessons(
    report: dict, approved: dict, courses: dict, rows: dict
) -> list[dict]:
    """Validate explicit empty historical occurrences separately from legacy IDs."""
    additions = approved.get("additional_lessons", [])
    if not isinstance(additions, list):
        raise AttendanceError("Новые занятия должны быть отдельным списком плана.")
    originals = {row["lesson_id"]: row for row in report["legacy_plan"]}
    seen_keys = set()
    topics = {
        (r["course_key"], r["topic_number"]): r["topic_title"].strip()
        for r in rows.values()
        if r.get("action") != "delete"
    }
    occupied = [
        (
            r["course_key"],
            r["local_day"],
            r["start_slot"],
            r["start_slot"] + courses[r["course_key"]]["duration"] - 1,
        )
        for r in rows.values()
        if r.get("action") != "delete" and r.get("merge_into") is None
    ]
    for addition in additions:
        key = addition.get("key")
        course = courses.get(addition.get("course_key"))
        source = rows.get(addition.get("roster_source_lesson_id"))
        if (
            not isinstance(key, str)
            or not key.strip()
            or key in seen_keys
            or course is None
            or source is None
            or source.get("action") == "delete"
            or source["course_key"] != addition["course_key"]
        ):
            raise AttendanceError(
                "Новому занятию нужны отдельный ключ и состав выбранного занятия того же курса."
            )
        seen_keys.add(key)
        if (
            type(addition.get("topic_number")) is not int
            or addition["topic_number"] < 1
            or not isinstance(addition.get("topic_title"), str)
            or not addition["topic_title"].strip()
            or type(addition.get("start_slot")) is not int
            or not 1 <= addition["start_slot"] <= 9 - course["duration"]
        ):
            raise AttendanceError("Укажите тему, название и допустимое время нового занятия.")
        try:
            day = date.fromisoformat(addition["local_day"])
        except (ValueError, TypeError, KeyError) as exc:
            raise AttendanceError("Укажите точную дату нового занятия.") from exc
        if addition.get("date_confirmed") is not True:
            raise AttendanceError("Подтвердите точную дату нового занятия.")
        if day.isoformat() != addition["local_day"]:
            raise AttendanceError("Дата нового занятия должна иметь формат ГГГГ-ММ-ДД.")
        topic_key = (addition["course_key"], addition["topic_number"])
        title = addition["topic_title"].strip()
        if topic_key in topics and topics[topic_key] != title:
            raise AttendanceError("Один номер темы курса должен иметь одинаковое название.")
        topics[topic_key] = title
        last_slot = addition["start_slot"] + course["duration"] - 1
        if any(
            c == addition["course_key"]
            and d == addition["local_day"]
            and addition["start_slot"] <= end
            and last_slot >= start
            for c, d, start, end in occupied
        ):
            raise AttendanceError("Новое занятие пересекается с другим занятием курса.")
        occupied.append(
            (addition["course_key"], addition["local_day"], addition["start_slot"], last_slot)
        )
        # A time zone is inherited from the explicitly selected historical source;
        # neither an untrusted field nor the current system zone changes history.
        addition["timezone"] = originals[source["lesson_id"]]["timezone"]
    return additions


def _create_additional_lessons(connection, additions, courses, actual_courses) -> None:
    """Create empty completed occurrences and fixed rosters, without attendance."""
    for addition in additions:
        course = courses[addition["course_key"]]
        course_id, year_id = actual_courses[addition["course_key"]]
        title = addition["topic_title"].strip()
        connection.execute(
            "INSERT INTO course_topics(course_id,number,title) VALUES(?,?,?) ON CONFLICT(course_id,number) DO NOTHING",
            (course_id, addition["topic_number"], title),
        )
        topic_id = connection.execute(
            "SELECT id FROM course_topics WHERE course_id=? AND number=?",
            (course_id, addition["topic_number"]),
        ).fetchone()[0]
        source = connection.execute(
            "SELECT sb.roster_id,sb.room FROM block_lessons bl JOIN scheduled_blocks sb ON sb.id=bl.block_id WHERE bl.lesson_id=?",
            (addition["roster_source_lesson_id"],),
        ).fetchone()
        if source is None:
            raise AttendanceError("Состав исходного занятия не перенесён; новое занятие отменено.")
        roster_id = connection.execute(
            "INSERT INTO teaching_rosters(discipline_id,academic_year_id,semester,name,kind,duration,mode) VALUES(?,?,?,?,?,?,'students')",
            (
                course["discipline_id"],
                year_id,
                course["semester"],
                f"Восстановленный состав {addition['local_day']}",
                course["kind"],
                course["duration"],
            ),
        ).lastrowid
        connection.execute(
            "INSERT INTO roster_students SELECT ?,student_id FROM roster_students WHERE roster_id=?",
            (roster_id, source[0]),
        )
        connection.execute("INSERT INTO course_rosters VALUES(?,?)", (course_id, roster_id))
        block_id = connection.execute(
            "INSERT INTO scheduled_blocks(roster_id,day,start_slot,timezone,title,room) VALUES(?,?,?,?,?,?)",
            (
                roster_id,
                addition["local_day"],
                addition["start_slot"],
                addition["timezone"],
                title,
                source[1],
            ),
        ).lastrowid
        zone = ZoneInfo(addition["timezone"])
        for offset in range(course["duration"]):
            start, end = SLOTS[addition["start_slot"] + offset - 1]

            def stamp(value, day=addition["local_day"], timezone=zone):
                return (
                    datetime.combine(date.fromisoformat(day), time.fromisoformat(value), timezone)
                    .astimezone(UTC)
                    .isoformat(timespec="microseconds")
                )

            pair_id = connection.execute(
                "INSERT INTO lessons(discipline_id,starts_at,ends_at,timezone,title,room,state,roster_fixed) VALUES(?,?,?,?,?,?,'completed',1)",
                (
                    course["discipline_id"],
                    stamp(start),
                    stamp(end),
                    addition["timezone"],
                    title,
                    source[1],
                ),
            ).lastrowid
            connection.execute(
                "INSERT INTO block_lessons VALUES(?,?,?)",
                (block_id, pair_id, addition["start_slot"] + offset),
            )
            connection.execute(
                "INSERT INTO lesson_roster SELECT ?,student_id FROM roster_students WHERE roster_id=?",
                (pair_id, roster_id),
            )
        connection.execute(
            "INSERT INTO course_lessons(course_id,topic_id,block_id) VALUES(?,?,?)",
            (course_id, topic_id, block_id),
        )


def apply_approved(source: Path, approved_path: Path, output: Path, *, mode="real") -> dict:
    from attendance_lab.infrastructure.migration import _fingerprints, rehearse
    from attendance_lab.infrastructure.sqlite import SQLiteRepository

    approved = json.loads(approved_path.read_text(encoding="utf-8"))
    report = rehearse(source, output, mode=mode)
    courses, rows = validate_decisions(report, approved)
    additions = validate_additional_lessons(report, approved, courses, rows)
    originals = {r["lesson_id"]: r for r in report["legacy_plan"]}
    repository = SQLiteRepository(output / "candidate.sqlite3", mode=mode)
    connection = repository._connection
    columns, _ = _fingerprints(connection)
    # Planning times intentionally change. Every other original column is immutable.
    _, immutable_before = _fingerprints(connection, columns)
    plan_hash = hashlib.sha256(approved_path.read_bytes()).hexdigest()
    try:
        with repository.transaction():
            actual_courses = {}
            for key, c in courses.items():
                connection.execute(
                    "INSERT INTO academic_years(start_year) VALUES(?) ON CONFLICT DO NOTHING",
                    (c["academic_year_start"],),
                )
                year_id = connection.execute(
                    "SELECT id FROM academic_years WHERE start_year=?", (c["academic_year_start"],)
                ).fetchone()[0]
                course_id = connection.execute(
                    "INSERT INTO semester_courses(discipline_id,academic_year_id,semester,study_course,kind,duration,reports_enabled,name) VALUES(?,?,?,?,?,?,0,?)",
                    (
                        c["discipline_id"],
                        year_id,
                        c["semester"],
                        c["study_course"],
                        c["kind"],
                        c["duration"],
                        c["name"].strip(),
                    ),
                ).lastrowid
                actual_courses[key] = (course_id, year_id)
            for lesson_id, r in rows.items():
                if r.get("action") == "delete" or r.get("merge_into") is not None:
                    continue
                c = courses[r["course_key"]]
                course_id, year_id = actual_courses[r["course_key"]]
                old = connection.execute(
                    "SELECT * FROM lessons WHERE id=?", (lesson_id,)
                ).fetchone()
                title = r.get("topic_title", "").strip()
                connection.execute(
                    "INSERT INTO course_topics(course_id,number,title) VALUES(?,?,?) ON CONFLICT(course_id,number) DO NOTHING",
                    (course_id, r["topic_number"], title),
                )
                topic_id = connection.execute(
                    "SELECT id FROM course_topics WHERE course_id=? AND number=?",
                    (course_id, r["topic_number"]),
                ).fetchone()[0]
                roster_id = connection.execute(
                    "INSERT INTO teaching_rosters(discipline_id,academic_year_id,semester,name,kind,duration,mode) VALUES(?,?,?,?,?,?,'students')",
                    (
                        c["discipline_id"],
                        year_id,
                        c["semester"],
                        f"Исторический состав занятия {lesson_id}",
                        c["kind"],
                        c["duration"],
                    ),
                ).lastrowid
                connection.execute("INSERT INTO course_rosters VALUES(?,?)", (course_id, roster_id))
                pairs = [lesson_id] + [
                    k for k, v in rows.items() if v.get("merge_into") == lesson_id
                ]
                if len(pairs) == 1 and c["duration"] == 2:
                    second = connection.execute(
                        "INSERT INTO lessons(discipline_id,starts_at,ends_at,timezone,title,room,state,roster_fixed) VALUES(?,?,?,?,?,?,?,?)",
                        (
                            old["discipline_id"],
                            old["starts_at"],
                            old["ends_at"],
                            old["timezone"],
                            old["title"],
                            old["room"],
                            old["state"],
                            old["roster_fixed"],
                        ),
                    ).lastrowid
                    pairs.append(second)
                    connection.execute(
                        "INSERT INTO lesson_roster SELECT ?,student_id FROM lesson_roster WHERE lesson_id=?",
                        (second, lesson_id),
                    )
                block_values = (
                    roster_id,
                    r["local_day"],
                    r["start_slot"],
                    old["timezone"],
                    old["title"],
                    old["room"],
                )
                block_id = originals[lesson_id].get("existing_block_id")
                if block_id is None:
                    block_id = connection.execute(
                        "INSERT INTO scheduled_blocks(roster_id,day,start_slot,timezone,title,room) VALUES(?,?,?,?,?,?)",
                        block_values,
                    ).lastrowid
                else:
                    old_roster = connection.execute(
                        "SELECT roster_id FROM scheduled_blocks WHERE id=?", (block_id,)
                    ).fetchone()[0]
                    connection.execute(
                        "INSERT INTO roster_students SELECT ?,student_id FROM roster_students WHERE roster_id=? ON CONFLICT DO NOTHING",
                        (roster_id, old_roster),
                    )
                    connection.execute(
                        "INSERT INTO roster_students SELECT ?,a.student_id FROM roster_groups rg JOIN annual_memberships a ON a.group_id=rg.group_id WHERE rg.roster_id=? ON CONFLICT DO NOTHING",
                        (roster_id, old_roster),
                    )
                    connection.execute(
                        "UPDATE scheduled_blocks SET roster_id=?,day=?,start_slot=?,timezone=?,title=?,room=? WHERE id=?",
                        (*block_values, block_id),
                    )
                zone = ZoneInfo(old["timezone"])
                for offset, pair_id in enumerate(pairs):
                    starts, ends = SLOTS[r["start_slot"] + offset - 1]

                    def stamp(value, day=r["local_day"], timezone=zone):
                        return (
                            datetime.combine(
                                date.fromisoformat(day), time.fromisoformat(value), timezone
                            )
                            .astimezone(UTC)
                            .isoformat(timespec="microseconds")
                        )

                    connection.execute(
                        "UPDATE lessons SET starts_at=?,ends_at=? WHERE id=?",
                        (stamp(starts), stamp(ends), pair_id),
                    )
                    connection.execute(
                        "INSERT INTO block_lessons VALUES(?,?,?) ON CONFLICT(block_id,lesson_id) DO UPDATE SET slot=excluded.slot",
                        (block_id, pair_id, r["start_slot"] + offset),
                    )
                    connection.execute(
                        "INSERT INTO roster_students SELECT ?,student_id FROM lesson_roster WHERE lesson_id=? ON CONFLICT DO NOTHING",
                        (roster_id, pair_id),
                    )
                connection.execute(
                    "INSERT INTO course_lessons(course_id,topic_id,block_id) VALUES(?,?,?)",
                    (course_id, topic_id, block_id),
                )
            _create_additional_lessons(connection, additions, courses, actual_courses)
            deleted = {key for key, r in rows.items() if r.get("action") == "delete"}
            if deleted:
                repository.ll_delete(
                    deleted,
                    datetime.now(UTC),
                    "Удаление тестовых занятий по утверждённому плану миграции",
                )
            connection.execute(
                "INSERT INTO metadata VALUES(?,?)",
                (
                    "approved_legacy_migration",
                    json.dumps(
                        {
                            "sha256": plan_hash,
                            "original_lessons": report["legacy_plan"],
                            "additional_lessons": additions,
                            "source_tables": report["tables"],
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
            repository._validate_database(connection)
            # Preserve original lesson IDs and all non-planned fields; adding an
            # empty second planned pair is the only permitted new history row.
            import sqlite3
            from contextlib import closing

            with closing(sqlite3.connect(output / "original.sqlite3")) as original:
                lesson_columns = [
                    row[1]
                    for row in original.execute("PRAGMA table_info(lessons)")
                    if row[1] not in ("starts_at", "ends_at")
                ]
                fields = ",".join(lesson_columns)
                for old_row in original.execute(f"SELECT {fields} FROM lessons"):
                    current = connection.execute(
                        f"SELECT {fields} FROM lessons WHERE id=?", (old_row[0],)
                    ).fetchone()
                    if current is None or tuple(current) != tuple(old_row):
                        raise AttendanceError("Исходные занятия изменены; применение отменено.")
            # Compare original rows by their original identities; newly added planned
            # lessons and links are permitted but existing evidence must be identical.
            for table in (
                "students",
                "cards",
                "scans",
                "binding_changes",
                "manual_batches",
                "manual_evidence",
                "card_evidence",
                "registration_sessions",
                "session_target_pairs",
                "session_scope_changes",
            ):
                _, after = _fingerprints(connection, {table: columns[table]})
                if after[table] != immutable_before[table]:
                    raise AttendanceError("Исходная история изменилась; применение отменено.")
        (output / "approved-plan.json").write_text(
            json.dumps(approved, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (output / "approved-plan.json").chmod(0o600)
        return {
            "blocks": len(additions)
            + sum(
                r.get("action") != "delete" and r.get("merge_into") is None for r in rows.values()
            ),
            "additional_lessons": len(additions),
            "deleted_lessons": sum(r.get("action") == "delete" for r in rows.values()),
            "lessons": len(rows),
            "integrity": "ok",
            "source_unchanged": True,
        }
    finally:
        repository.close()


def install_approved_copy(database: Path, candidate: Path, *, mode="real") -> None:
    """Install a reviewed copy only if the locked live source has not changed.

    The caller must hold the application's QLockFile. SQLite backup supplies both
    the pre-install safety copy and the atomic SQLite destination transaction.
    """
    from contextlib import closing

    from attendance_lab.infrastructure.migration import (
        _fingerprints,
        _open_readonly,
        _validate,
        rollback,
    )

    with closing(_open_readonly(candidate)) as connection:
        _validate(connection, mode)
        row = connection.execute(
            "SELECT value FROM metadata WHERE key='approved_legacy_migration'"
        ).fetchone()
        if row is None:
            raise AttendanceError("Копия не содержит утверждённого переноса занятий.")
        record = json.loads(row[0])
        with closing(_open_readonly(database)) as current:
            _validate(current, mode)
            _, actual = _fingerprints(current)
            if actual != record.get("source_tables"):
                raise AttendanceError(
                    "Рабочая база изменилась после репетиции. Создайте новый план."
                )
    # Exact-version restore creates a verified pre-install safety copy first.
    rollback(database, candidate, mode=mode)
