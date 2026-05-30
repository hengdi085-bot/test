import os
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    MessageHandler,
    CommandHandler,
    ContextTypes,
    filters,
)

TOKEN = os.getenv("TOKEN")
TZ = timezone(timedelta(hours=8))

# 上班人员
workers = {}

# 离岗人员
away = {}

# 迟到人员
late_workers = {}

# 指令限制时间
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
    # 早上 05:00 - 下午 16:00 打 sb 算白班
    if 5 <= t.hour < 16:
        shift = "白班"
        start_time = t.replace(hour=9, minute=0, second=0, microsecond=0)
        late_time = t.replace(hour=10, minute=0, second=0, microsecond=0)
        off_time = t.replace(hour=21, minute=0, second=0, microsecond=0)
    else:
        shift = "夜班"
        start_time = t.replace(hour=21, minute=0, second=0, microsecond=0)
        late_time = t.replace(hour=22, minute=0, second=0, microsecond=0)
        off_time = (t + timedelta(days=1)).replace(
            hour=9, minute=0, second=0, microsecond=0
        )

    return shift, start_time, late_time, off_time


def worker_list_text():
    names = [v["name"] for v in workers.values()]
    if not names:
        return "暂无"

    return " ".join(names)


def online_summary():
    return (
        f"远程当天上班总人数：{len(workers)}\n"
        f"员工名单：{worker_list_text()}"
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "打卡机器人已启动。\n\n"
        "上班：sb\n"
        "下班：xb\n"
        "上厕所：wc\n"
        "抽烟：cy\n"
        "吃饭：cf\n"
        "出去：cq\n"
        "回来：1\n\n"
        "查看人数：rs\n"
        "查看离岗：zt\n"
        "查看迟到：cd"
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

    # 上班
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
            msg += f"⚠️ 状态：迟到 {late_minutes} 分钟\n"
        else:
            msg += "状态：正常\n"

        msg += "\n" + online_summary()

        await update.message.reply_text(msg)
        return

    # 下班
    if text == "xb":
        if uid not in workers:
            await update.message.reply_text(f"⚠️ {name} 你还没有上班打卡。")
            return

        info = workers.pop(uid)
        away.pop(uid, None)

        work_time = t - info["start"]
        hours = int(work_time.total_seconds() // 3600)
        minutes = int((work_time.total_seconds() % 3600) // 60)

        await update.message.reply_text(
            f"✅ {name} 下班打卡成功\n"
            f"班次：{info['shift']}\n"
            f"下班时间：{t.strftime('%H:%M')}\n"
            f"工作时长：{hours}小时{minutes}分钟\n\n"
            + online_summary()
        )

        # 如果没人上班了，1小时后清空
        if len(workers) == 0:
            async def clear_later():
                await asyncio.sleep(60 * 60)
                if len(workers) == 0:
                    away.clear()
                    late_workers.clear()

            asyncio.create_task(clear_later())

        return

    # 离岗：wc cy cf cq
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
            f"回来请回复：1"
        )

        async def check_timeout(user_id):
            await asyncio.sleep(limit * 60)

            info = away.get(user_id)
            if info:
                used = int((now() - info["start"]).total_seconds() // 60)
                await context.bot.send_message(
                    chat_id=info["chat_id"],
                    text=(
                        f"⚠️ 超时未归\n\n"
                        f"员工：{info['name']}\n"
                        f"状态：{info['action']}\n"
                        f"限制时间：{info['limit']}分钟\n"
                        f"当前已用：{used}分钟"
                    ),
                )

        asyncio.create_task(check_timeout(uid))
        return

    # 返回岗位
    if text == "1":
        if uid not in away:
            await update.message.reply_text(f"{name} 当前没有离岗记录。")
            return

        info = away.pop(uid)
        used = int((t - info["start"]).total_seconds() // 60)

        status = "正常"
        if used > info["limit"]:
            status = f"超时 {used - info['limit']} 分钟"

        await update.message.reply_text(
            f"✅ {name} 已返回岗位\n"
            f"项目：{info['action']}\n"
            f"用时：{used}分钟\n"
            f"状态：{status}"
        )
        return

    # 查看人数
    if text in ["rs", "/rs"]:
        await update.message.reply_text(online_summary())
        return

    # 查看离岗
    if text in ["zt", "/zt"]:
        if not away:
            await update.message.reply_text("当前无人离岗。")
            return

        lines = []
        for info in away.values():
            used = int((t - info["start"]).total_seconds() // 60)
            lines.append(
                f"{info['name']}：{info['action']}，已用 {used} 分钟，限制 {info['limit']} 分钟"
            )

        await update.message.reply_text("当前离岗人员：\n" + "\n".join(lines))
        return

    # 查看迟到
    if text in ["cd", "/cd"]:
        if not late_workers:
            await update.message.reply_text("今日迟到人员：暂无")
            return

        lines = []
        for info in late_workers.values():
            lines.append(
                f"{info['name']}：{info['shift']}，迟到 {info['minutes']} 分钟"
            )

        await update.message.reply_text("今日迟到人员：\n" + "\n".join(lines))
        return


app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle_message))

app.run_polling()
