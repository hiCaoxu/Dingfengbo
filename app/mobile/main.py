"""手机端界面：底部五项，外加同步设置。"""

from __future__ import annotations

import logging
from datetime import date

import flet as ft

from .. import theme as th
from ..config import DEFAULT_SERVER_URL, LOGS_DIR, load_settings, save_settings
from ..db.session import init_db
from ..icons import ico
from ..logging_setup import setup_logging
from ..services.sync import probe, sync_client
from . import records, scheduler

logger = logging.getLogger(__name__)

_TABS = ("todo", "diary", "notes", "health", "finance")


def _snack(page: ft.Page, message: str) -> None:
    bar = ft.SnackBar(ft.Text(message, color=th.ON_PRIMARY, size=14), bgcolor=th.PRIMARY)
    show = getattr(page, "show_dialog", None)
    if callable(show):
        show(bar)
        return
    page.snack_bar = bar
    page.snack_bar.open = True
    page.update()


def _field(label: str, value: str = "", multiline: bool = False) -> ft.TextField:
    return ft.TextField(
        label=label,
        value=value,
        multiline=multiline,
        min_lines=8 if multiline else None,
        text_size=th.FONT_BODY,
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        label_style=ft.TextStyle(color=th.TEXT_MUTED, size=th.FONT_CAPTION),
    )


def _dropdown(label: str, options: list[tuple[str, str]], value: str) -> ft.Dropdown:
    return ft.Dropdown(
        label=label,
        value=value,
        options=[ft.dropdown.Option(key=key, text=text) for key, text in options],
        border_color=th.DIVIDER,
        focused_border_color=th.PRIMARY,
        color=th.TEXT,
        text_size=th.FONT_BODY,
    )


def _save_button(label: str, handler) -> ft.FilledButton:
    return ft.FilledButton(
        label,
        on_click=handler,
        style=ft.ButtonStyle(bgcolor=th.PRIMARY, color=th.ON_PRIMARY),
    )


def _page_column(controls: list) -> ft.Column:
    return ft.Column(controls, spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)


def main(page: ft.Page) -> None:
    setup_logging(LOGS_DIR)
    init_db()
    settings = load_settings()
    th.set_mode(settings.get("theme_mode", "light"))
    th.apply_fonts()

    page.title = "定风波"
    page.bgcolor = th.BG
    page.padding = 0
    page.theme = th.build_theme()
    page.dark_theme = th.build_theme(seed=th.PALETTES["dark"]["PRIMARY"])
    page.theme_mode = ft.ThemeMode.DARK if th.is_dark() else ft.ThemeMode.LIGHT
    platform = str(getattr(page, "platform", "") or "")
    if "android" not in platform.lower() and "ios" not in platform.lower():
        page.window.width = 400
        page.window.height = 800

    state = {"tab": "todo", "settings": False, "note_id": "", "todo_id": ""}
    status = ft.Text(sync_client.status, size=th.FONT_CAPTION, color=th.TEXT_MUTED, expand=True)
    body = ft.Container(expand=True)

    def show_status() -> None:
        status.value = sync_client.status
        try:
            # 每 5 分钟同步一次。只刷这一行，避免整页重绘把窗口闪一下。
            status.update()
        except Exception:
            try:
                page.update()
            except Exception:
                logger.debug("刷新同步状态失败，页面可能已关闭", exc_info=True)

    def fail(exc: Exception) -> None:
        _snack(page, str(exc) if isinstance(exc, records.RecordError) else "保存失败，请稍后重试")
        if not isinstance(exc, records.RecordError):
            logger.exception("手机端保存失败")

    def render() -> None:
        if state["settings"]:
            body.content = _settings_view()
        else:
            body.content = {
                "todo": _todo_view,
                "diary": _diary_view,
                "notes": _notes_view,
                "health": _health_view,
                "finance": _finance_view,
            }[state["tab"]]()
        page.update()

    def select_tab(event) -> None:
        state["tab"] = _TABS[int(event.control.selected_index)]
        state["settings"] = False
        state["note_id"] = ""
        state["todo_id"] = ""
        render()

    def open_settings(_event) -> None:
        state["settings"] = True
        render()

    def _close_settings() -> None:
        state["settings"] = False
        render()

    nav = ft.NavigationBar(
        selected_index=0,
        destinations=[
            ft.NavigationBarDestination(icon=ico("TASK_ALT"), label="任务"),
            ft.NavigationBarDestination(icon=ico("AUTO_STORIES"), label="日记"),
            ft.NavigationBarDestination(icon=ico("DESCRIPTION"), label="笔记"),
            ft.NavigationBarDestination(icon=ico("FAVORITE"), label="健康"),
            ft.NavigationBarDestination(icon=ico("PAYMENTS"), label="记账"),
        ],
        on_change=select_tab,
        bgcolor=th.CARD,
        indicator_color=th.PRIMARY_LIGHT,
    )

    def _todo_view() -> ft.Control:
        if state["todo_id"]:
            return _todo_form()
        items = records.open_todos()
        tiles: list[ft.Control] = []
        if not items:
            tiles.append(ft.Text("没有未完成的任务", color=th.TEXT_MUTED, size=th.FONT_BODY))
        for item in items:
            meta = " ".join(part for part in (item["due_date"], item["priority_label"]) if part)
            tiles.append(
                ft.Row(
                    [
                        ft.Checkbox(
                            value=False,
                            on_change=lambda event, todo_id=item["id"]: _complete(todo_id, event),
                        ),
                        ft.Column(
                            [
                                ft.TextButton(
                                    item["title"],
                                    on_click=lambda event, todo_id=item["id"]: _edit_todo(todo_id),
                                ),
                                ft.Text(meta, size=th.FONT_CAPTION, color=th.TEXT_MUTED),
                            ],
                            spacing=0,
                            expand=True,
                        ),
                    ],
                    spacing=4,
                )
            )
        tiles.append(_save_button("新建任务", lambda _event: _edit_todo("new")))
        return _page_column(tiles)

    def _edit_todo(todo_id: str) -> None:
        state["todo_id"] = todo_id
        render()

    def _complete(todo_id: str, event) -> None:
        if not event.control.value:
            return
        try:
            records.complete_todo(todo_id)
        except Exception as exc:
            fail(exc)
            return
        _snack(page, "已完成")
        render()

    def _todo_form() -> ft.Control:
        current = next(
            (item for item in records.open_todos() if item["id"] == state["todo_id"]),
            None,
        )
        title = _field("标题", current["title"] if current else "")
        due = _field("日期 YYYY-MM-DD", current["due_date"] if current else date.today().isoformat())
        selected_priority = current["priority"] if current and current["priority"] else "none"
        priority = _dropdown(
            "优先级",
            [("none" if key == "" else key, label) for key, label in records.PRIORITIES],
            selected_priority,
        )

        def save(_event) -> None:
            try:
                day = records.parse_day(due.value) if (due.value or "").strip() else None
                code = "" if (priority.value or "none") == "none" else str(priority.value)
                if current is None:
                    records.create_todo(title.value or "", day, code)
                else:
                    records.update_todo(current["id"], title.value or "", day, code)
            except Exception as exc:
                fail(exc)
                return
            state["todo_id"] = ""
            _snack(page, "已保存")
            render()

        def cancel(_event) -> None:
            state["todo_id"] = ""
            render()

        return _page_column(
            [
                ft.Text("编辑任务" if current else "新建任务", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                title,
                due,
                priority,
                ft.Row([_save_button("保存", save), ft.TextButton("取消", on_click=cancel)], spacing=8),
            ]
        )

    def _diary_view() -> ft.Control:
        today = date.today()
        loaded = records.load_diary(today)
        content = _field("正文", loaded["content"], multiline=True)

        def save(_event) -> None:
            try:
                records.save_diary(content.value or "", today, loaded["id"])
            except Exception as exc:
                fail(exc)
                return
            if (content.value or "").strip() or loaded["id"]:
                _snack(page, "已保存")
            else:
                _snack(page, "空日记未保存")
            render()

        return _page_column(
            [
                ft.Text(loaded["title"], size=th.FONT_SECTION_TITLE, color=th.TEXT, weight="bold"),
                content,
                _save_button("保存", save),
            ]
        )

    def _notes_view() -> ft.Control:
        if state["note_id"]:
            note = records.read_note(state["note_id"])
            if note is None:
                state["note_id"] = ""
            else:
                from ..ui.notes import render_markdown

                content = note["content"] or ""
                body = (
                    render_markdown(content, page)
                    if content.strip()
                    else ft.Text("（空）", size=th.FONT_BODY, color=th.TEXT_MUTED)
                )
                return ft.Column(
                    [
                        ft.TextButton("返回", on_click=lambda _event: _close_note()),
                        ft.Text(note["title"], size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                        body,
                    ],
                    spacing=12,
                    expand=True,
                )
        notes = records.list_notes()
        if not notes:
            return _page_column([ft.Text("还没有笔记。在电脑上写好后，同步即可在这里阅读。", color=th.TEXT_MUTED)])
        return _page_column(
            [
                ft.TextButton(
                    item["title"],
                    on_click=lambda _event, note_id=item["id"]: _open_note(note_id),
                )
                for item in notes
            ]
        )

    def _open_note(note_id: str) -> None:
        state["note_id"] = note_id
        render()

    def _close_note() -> None:
        state["note_id"] = ""
        render()

    def _health_view() -> ft.Control:
        kind = _dropdown("记录", [("exercise", "运动"), ("diet", "饮食"), ("weight", "体重"), ("sleep", "睡眠")], "exercise")
        exercise_kind = _dropdown("运动类型", [(name, name) for name in records.EXERCISE_KINDS], "跑步")
        minutes = _field("时长（分钟）", "30")
        distance = _field("距离 km（可选）")
        exercise_kcal = _field("消耗 kcal（可留空自动估算）")
        meal = _dropdown("餐次", [(name, name) for name in records.MEALS], "早餐")
        food = _field("食物")
        grams = _field("重量 g", "100")
        diet_kcal = _field("热量 kcal（可留空按食物库计算）")
        diet_note = _field("备注")
        kg = _field("体重 kg")
        waist = _field("腰围 cm（可选）")
        fat = _field("体脂率 %（可选）")
        bedtime = _field("入睡 HH:MM", "23:00")
        wake = _field("醒来 HH:MM", "07:00")
        quality = _dropdown("质量", [(str(score), str(score)) for score in range(1, 6)], "3")
        day = _field("日期 YYYY-MM-DD", date.today().isoformat())
        panels = {
            "exercise": ft.Column([exercise_kind, minutes, distance, exercise_kcal], spacing=8),
            "diet": ft.Column([meal, food, grams, diet_kcal, diet_note], spacing=8),
            "weight": ft.Column([kg, waist, fat], spacing=8),
            "sleep": ft.Column([bedtime, wake, quality], spacing=8),
        }
        holder = ft.Column([panels["exercise"]], spacing=8)

        def switch(event) -> None:
            holder.controls = [panels[event.control.value or "exercise"]]
            page.update()

        kind.on_select = switch

        def save(_event) -> None:
            try:
                record_day = records.parse_day(day.value)
                selected = kind.value or "exercise"
                if selected == "exercise":
                    records.save_exercise(
                        record_day,
                        exercise_kind.value or "跑步",
                        minutes.value or "",
                        distance.value,
                        exercise_kcal.value,
                        "",
                    )
                elif selected == "diet":
                    records.save_diet(
                        record_day,
                        meal.value or "早餐",
                        food.value or "",
                        grams.value or "",
                        diet_kcal.value,
                        diet_note.value or "",
                    )
                elif selected == "weight":
                    records.save_weight(record_day, kg.value or "", waist.value, fat.value)
                else:
                    records.save_sleep(record_day, bedtime.value or "", wake.value or "", int(quality.value or "3"))
            except Exception as exc:
                fail(exc)
                return
            _snack(page, "已记下")

        return _page_column(
            [
                ft.Text("记一笔健康数据", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                kind,
                holder,
                day,
                _save_button("保存", save),
            ]
        )

    def _finance_view() -> ft.Control:
        accounts = records.list_accounts()
        if not accounts:
            return _page_column(
                [
                    ft.Text("记账", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                    ft.Text("请先在电脑上建一个账户，同步后再记一笔。", color=th.TEXT_MUTED, size=th.FONT_BODY),
                ]
            )
        kind = _dropdown("类型", [("expense", "支出"), ("income", "收入")], "expense")
        amount = _field("金额（元）")
        category = _dropdown("分类", [(name, name) for name in records.EXPENSE_CATEGORIES], "餐饮")
        account = _dropdown("账户", [(item["id"], item["name"]) for item in accounts], accounts[0]["id"])
        day = _field("日期 YYYY-MM-DD", date.today().isoformat())
        note = _field("备注")

        def switch_kind(event) -> None:
            names = records.INCOME_CATEGORIES if event.control.value == "income" else records.EXPENSE_CATEGORIES
            category.options = [ft.dropdown.Option(key=name, text=name) for name in names]
            category.value = names[0]
            page.update()

        kind.on_select = switch_kind

        def save(_event) -> None:
            try:
                records.save_transaction(
                    kind.value or "expense",
                    amount.value or "",
                    category.value or "",
                    account.value or "",
                    records.parse_day(day.value),
                    note.value or "",
                )
            except Exception as exc:
                fail(exc)
                return
            amount.value = ""
            note.value = ""
            _snack(page, "已记下")
            page.update()

        return _page_column(
            [
                ft.Text("记一笔", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                kind,
                amount,
                category,
                account,
                day,
                note,
                _save_button("保存", save),
            ]
        )

    def _settings_view() -> ft.Control:
        current = load_settings()
        server = _field("同步服务地址", str(current.get("server_url") or DEFAULT_SERVER_URL))
        token = _field("同步令牌", str(current.get("sync_token") or ""), multiline=False)
        token.password = True
        token.can_reveal_password = True

        def save(_event) -> None:
            try:
                save_settings(
                    {
                        "server_url": (server.value or DEFAULT_SERVER_URL).strip(),
                        "sync_token": (token.value or "").strip(),
                        "sync_enabled": True,
                        "allow_weak_secret_storage": True,
                    }
                )
            except Exception:
                logger.exception("保存同步设置失败")
                _snack(page, "设置没有保存")
                return
            _snack(page, "已保存")

        def test_connection(_event) -> None:
            address = (server.value or "").strip()
            secret = (token.value or "").strip()

            def worker() -> None:
                message = probe(address, secret)

                async def apply() -> None:
                    _snack(page, message)

                page.run_task(apply)

            page.run_thread(worker)

        def sync_now(_event) -> None:
            def worker() -> None:
                try:
                    sync_client.sync_once()
                    message = sync_client.status
                except Exception:
                    logger.exception("手动同步失败")
                    message = "同步失败，请稍后重试"

                async def apply() -> None:
                    status.value = sync_client.status
                    _snack(page, message)

                page.run_task(apply)

            page.run_thread(worker)

        return _page_column(
            [
                ft.TextButton("返回", on_click=lambda _event: _close_settings()),
                ft.Text("同步", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
                ft.Text(
                    "这台设备没有 Windows 加密。保存后，同步令牌以可解码形式放在应用私有目录。",
                    size=th.FONT_CAPTION,
                    color=th.TEXT_MUTED,
                ),
                server,
                token,
                ft.Row(
                    [
                        _save_button("保存", save),
                        ft.TextButton("测试连接", on_click=test_connection),
                        ft.TextButton("立即同步", on_click=sync_now),
                    ],
                    wrap=True,
                    spacing=8,
                ),
            ]
        )

    header = ft.Row(
        [
            ft.Text("定风波", size=th.FONT_PAGE_TITLE, color=th.TEXT, weight="bold"),
            status,
            ft.IconButton(icon=ico("SETTINGS"), tooltip="同步设置", icon_color=th.TEXT_MUTED, on_click=open_settings),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )
    # 红米等机型会把内容画进状态栏。只留 12 像素时，右上角设置键被系统栏挡住，点不到。
    mobile_os = "android" in platform.lower() or "ios" in platform.lower()
    page.add(
        ft.SafeArea(
            content=ft.Container(
                content=ft.Column([header, body, nav], expand=True, spacing=8),
                padding=12,
                expand=True,
            ),
            expand=True,
            minimum_padding=ft.Padding.only(top=48) if mobile_os else 0,
        )
    )
    scheduler.bind(page, show_status)
    render()


def run() -> None:
    if hasattr(ft, "run"):
        ft.run(main)
    else:
        ft.app(target=main)
