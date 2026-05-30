import os
import asyncio
from datetime import datetime, timedelta, timezone

from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TOKEN = os.getenv("TOKEN")
TZ = timezone(timedelta(hours=8))

workers = {}
away = {}

LIMITS = {
    "wc": ("上厕所", 15),
    "cy": ("抽烟", 10),
    "cf": ("吃饭", 35),
    "cq": ("出去", 10),
}

def now():
    return datetime.now(TZ)

def name_of(user):
    return user.full_name or user.username or str(user.id)

def shift_type(t):
    if 5 <= t.hour < 16:
        return "白班", t.replace(hour=9, minute=0, second=0, microsecond=0), t.replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        start = t.replace(hour=21, minute=0, second=0, microsecond=0)
        late = t.replace(hour=22, minute=0, second=0, microsecond=0)
        return "夜班", start, late

def online_text():
    names = [v["name"] for v in workers.values()]
    return f"远程当天上班总人数：{len(names)}\n员工名单：{' '.join(names) if names else '暂无'}"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("打卡机器人已启动。发送 sb 上班，xb 下班。")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    user = update.message.from_user
    uid = user.id
    name = name_of(user)
    t = now()

    if text == "sb":
        shift, start_time, late_time = shift_type(t)
        is_late = t > late_time

        workers[uid] = {
            "name": name,
            "start": t,
            "shift": shift,
            "late": is_late,
        }

        msg = f"✅ {name} 上班打卡成功\n班次：{shift}\n"
        if is_late:
            mins = int((t - late_time).total_seconds() // 60)
            msg += f"⚠️ 已迟到：{mins}分钟\n"
        msg += "\n" + online_text()

        await update.message.reply_text(msg)
        return

    if text == "xb":
        if uid not in workers:
            await update.message.reply_text(f"{name} 你还没有上班打卡。")
            return

        start = workers[uid]["start"]
        work_time = t - start

        del workers[uid]
        away.pop(uid, None)

        await update.message.reply_text(
            f"✅ {name} 下班打卡成功\n"
            f"工作时长：{str(work_time).split('.')[0]}\n\n"
            + online_text()
        )
        return

    if text in LIMITS:
        if uid not in workers:
            await update.message.reply_text(f"{name} 你还没上班打卡，不能使用 {text}。")
            return

        action, limit = LIMITS[text]
        away[uid] = {
            "name": name,
            "action": action,
            "start": t,
            "limit": limit,
            "chat_id": update.message.chat_id,
        }

        await update.message.reply_text(
            f"⏳ {name} {action}\n"
            f"限制时间：{limit}分钟\n"
            f"回来请回复：1"
        )

        async def check_timeout():
            await asyncio.sleep(limit * 60)
            info = away.get(uid)
            if info:
                await context.bot.send_message(
                    chat_id=info["chat_id"],
                    text=f"⚠️ 超时未归\n员工：{info['name']}\n状态：{info['action']}\n已超过：{info['limit']}分钟"
                )

        asyncio.create_task(check_timeout())
        return

    if text == "1":
        if uid not in away:
            await update.message.reply_text(f"{name} 当前没有离岗记录。")
            return

        info = away.pop(uid)
        used = int((t - info["start"]).total_seconds() // 60)

        await update.message.reply_text(
            f"✅ {name} 已返回岗位\n"
            f"状态：{info['action']}\n"
            f"用时：{used}分钟"
        )
        return

    if text == "/rs" or text == "rs":
        await update.message.reply_text(online_text())
        return

    if text == "/zt" or text == "zt":
        if not away:
            await update.message.reply_text("当前无人离岗。")
            return

        lines = []
        for info in away.values():
            used = int((t - info["start"]).total_seconds() // 60)
            lines.append(f"{info['name']}：{info['action']}，已用 {used} 分钟")

        await update.message.reply_text("当前离岗人员：\n" + "\n".join(lines))
        return

    if text == "/cd" or text == "cd":
        late_names = [v["name"] for v in workers.values() if v["late"]]
        await update.message.reply_text(
            "今日迟到人员：\n" + ("\n".join(late_names) if late_names else "暂无")
        )
        return

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle))

app.run_polling()
