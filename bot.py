import os
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TOKEN")
TZ = timezone(timedelta(hours=8))

workers = {}
away = {}
late_workers = {}
off_workers = {}

LIMITS = {
    "wc": ("上厕所", 15),
    "cy": ("抽烟", 10),
    "cf": ("吃饭", 35),
    "cq": ("出去", 10),
}


def now():
    return datetime.now(TZ)


def get_name(user):
    return user.full_name or user.username or str(user.id)


def get_shift(t):
    if 9 <= t.hour < 21:
        shift = "白班"
        start = t.replace(hour=9, minute=0, second=0, microsecond=0)
        late = t.replace(hour=10, minute=0, second=0, microsecond=0)
        off = t.replace(hour=21, minute=0, second=0, microsecond=0)
    else:
        shift = "夜班"
        start = t.replace(hour=21, minute=0, second=0, microsecond=0)
        late = t.replace(hour=22, minute=0, second=0, microsecond=0)
        off = (t + timedelta(days=1)).replace(hour=9, minute=0, second=0, microsecond=0)
    return shift, start, late, off


def list_button():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👥 查看上班名单", callback_data="list_workers")],
        [InlineKeyboardButton("🚶 查看离岗名单", callback_data="list_away")],
        [InlineKeyboardButton("⏰ 查看迟到名单", callback_data="list_late")],
        [InlineKeyboardButton("✅ 查看下班名单", callback_data="list_off")],
    ])


def summary_text():
    return f"远程当天上班总人数：{len(workers)}"


async def send_summary(message, text):
    await message.reply_text(text + "\n\n" + summary_text(), reply_markup=list_button())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "打卡机器人已启动\n\n"
        "上班：sb\n"
        "下班：xb\n"
        "上厕所：wc\n"
        "抽烟：cy\n"
        "吃饭：cf\n"
        "出去：cq\n"
        "回来：1\n\n"
        "查看人数：rs\n"
        "查看离岗：zt\n"
        "查看迟到：cd",
        reply_markup=list_button()
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip().lower()
    user = update.message.from_user
    uid = user.id
    name = get_name(user)
    chat_id = update.message.chat_id
    t = now()

    if text == "sb":
        shift, start_time, late_time, off_time = get_shift(t)

        is_late = t > late_time
        late_minutes = 0

        if is_late:
            late_minutes = int((t - late_time).total_seconds() // 60)
            late_workers[uid] = {
                "name": name,
                "minutes": late_minutes,
                "time": t,
                "shift": shift,
            }

        workers[uid] = {
            "name": name,
            "start": t,
            "shift": shift,
            "late": is_late,
            "off_time": off_time,
        }

        msg = (
            f"✅ {name} 上班打卡成功\n"
            f"班次：{shift}\n"
            f"打卡时间：{t.strftime('%H:%M')}\n"
        )

        if is_late:
            msg += f"⚠️ 状态：迟到 {late_minutes} 分钟"
        else:
            msg += "状态：正常"

        await send_summary(update.message, msg)
        return

    if text == "xb":
        if uid not in workers:
            await update.message.reply_text(f"⚠️ {name} 你还没有上班打卡。")
            return

        info = workers.pop(uid)
        away.pop(uid, None)

        work_time = t - info["start"]
        hours = int(work_time.total_seconds() // 3600)
        minutes = int((work_time.total_seconds() % 3600) // 60)

        off_workers[uid] = {
            "name": name,
            "time": t,
            "shift": info["shift"],
            "work": f"{hours}小时{minutes}分钟",
        }

        msg = (
            f"✅ {name} 下班打卡成功\n"
            f"班次：{info['shift']}\n"
            f"下班时间：{t.strftime('%H:%M')}\n"
            f"工作时长：{hours}小时{minutes}分钟"
        )

        await send_summary(update.message, msg)

        if len(workers) == 0:
            async def clear_later():
                await asyncio.sleep(60 * 60)
                if len(workers) == 0:
                    away.clear()
                    late_workers.clear()
                    off_workers.clear()

            asyncio.create_task(clear_later())

        return

    if text in LIMITS:
        if uid not in workers:
            await update.message.reply_text(f"⚠️ {name} 你还没有上班打卡，不能离岗。")
            return

        action, limit = LIMITS[text]

        away[uid] = {
            "name": name,
            "action": action,
            "start": t,
            "limit": limit,
            "chat_id": chat_id,
        }

        await update.message.reply_text(
            f"⏳ {name} 开始{action}\n"
            f"限制时间：{limit}分钟\n"
            f"回来请回复：1",
            reply_markup=list_button()
        )

        async def check_timeout(user_id):
            await asyncio.sleep(limit * 60)

            info = away.get(user_id)
            if info:
                used = int((now() - info["start"]).total_seconds() // 60)
                await context.bot.send_message(
                    chat_id=info["chat_id"],
                    text=(
                        f"⚠️ 离岗超时提醒\n\n"
                        f"员工：{info['name']}\n"
                        f"项目：{info['action']}\n"
                        f"限制时间：{info['limit']}分钟\n"
                        f"当前已用：{used}分钟\n\n"
                        f"请尽快返回岗位，回来请回复：1"
                    ),
                    reply_markup=list_button()
                )

        asyncio.create_task(check_timeout(uid))
        return

    if text == "1":
        if uid not in away:
            await update.message.reply_text(f"{name} 当前没有离岗记录。")
            return

        info = away.pop(uid)
        used = int((t - info["start"]).total_seconds() // 60)

        if used > info["limit"]:
            status = f"超时 {used - info['limit']} 分钟"
        else:
            status = "正常"

        await update.message.reply_text(
            f"✅ {name} 已返回岗位\n"
            f"项目：{info['action']}\n"
            f"用时：{used}分钟\n"
            f"状态：{status}",
            reply_markup=list_button()
        )
        return

    if text in ["rs", "/rs"]:
        await update.message.reply_text(summary_text(), reply_markup=list_button())
        return

    if text in ["zt", "/zt"]:
        await show_away(update.message)
        return

    if text in ["cd", "/cd"]:
        await show_late(update.message)
        return


async def show_workers(target):
    if not workers:
        text = "当前上班员工名单：暂无"
    else:
        lines = []
        for i, info in enumerate(workers.values(), 1):
            lines.append(f"{i}. {info['name']}｜{info['shift']}｜{info['start'].strftime('%H:%M')}")
        text = "当前上班员工名单：\n\n" + "\n".join(lines) + f"\n\n总人数：{len(workers)}"

    await target.reply_text(text)


async def show_away(target):
    if not away:
        await target.reply_text("当前离岗人员：暂无")
        return

    t = now()
    lines = []
    for i, info in enumerate(away.values(), 1):
        used = int((t - info["start"]).total_seconds() // 60)
        lines.append(f"{i}. {info['name']}｜{info['action']}｜已用 {used} 分钟｜限制 {info['limit']} 分钟")

    await target.reply_text("当前离岗人员：\n\n" + "\n".join(lines))


async def show_late(target):
    if not late_workers:
        await target.reply_text("今日迟到人员：暂无")
        return

    lines = []
    for i, info in enumerate(late_workers.values(), 1):
        lines.append(f"{i}. {info['name']}｜{info['shift']}｜迟到 {info['minutes']} 分钟")

    await target.reply_text("今日迟到人员：\n\n" + "\n".join(lines))


async def show_off(target):
    if not off_workers:
        await target.reply_text("今日下班人员：暂无")
        return

    lines = []
    for i, info in enumerate(off_workers.values(), 1):
        lines.append(f"{i}. {info['name']}｜{info['shift']}｜{info['time'].strftime('%H:%M')}｜{info['work']}")

    await target.reply_text("今日下班人员：\n\n" + "\n".join(lines))


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "list_workers":
        await show_workers(query.message)

    elif data == "list_away":
        await show_away(query.message)

    elif data == "list_late":
        await show_late(query.message)

    elif data == "list_off":
        await show_off(query.message)


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))
app.add_handler(MessageHandler(filters.TEXT, handle_message))

app.run_polling()
