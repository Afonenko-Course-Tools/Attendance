import QtQuick

EntityPickerField {
    id: field
    property bool allowAll: false
    property var lessons: []
    rows: {
        const items = (lessons || []).map(lesson => {
            const starts = String(lesson.starts_at || "");
            const day = lesson.date || starts.slice(0, 10);
            const time = lesson.time || starts.slice(11, 16);
            const dateLabel = day.split("-").reverse().join(".");
            const archived = lesson.deleted ? " · удалено" : "";
            const label = lesson.label || lesson.name || ([dateLabel, time, lesson.roster_name].filter(Boolean).join(" · ") + archived);
            const group = lesson.group || lesson.roster_name || "";
            const topic = lesson.topic ? lesson.topic + archived : label;
            return Object.assign({}, lesson, {
                id: lesson.id ?? lesson.occurrence_id,
                date: day,
                date_label: dateLabel,
                time: time,
                topic: topic,
                group: group,
                label: label,
                search_text: [day, day.split("-").reverse().join("."), time, topic, group, label].join(" ")
            });
        });
        return allowAll ? [{id: 0, date: "", time: "", topic: "Все занятия", group: "", label: "Все занятия"}].concat(items) : items;
    }
    columns: [
        {key: "date_label", sortKey: "date", title: "Дата", minimum: 115, center: true},
        {key: "time", title: "Время", minimum: 70, center: true},
        {key: "topic", title: "Тема", minimum: 120, stretch: true},
        {key: "group", title: "Группа / состав", minimum: 100}
    ]
    labelField: "label"
    pickerTitle: "Выберите занятие"
    searchPlaceholder: "Дата, тема или группа"
}
