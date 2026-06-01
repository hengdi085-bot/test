import os
import asyncio
import threading
from datetime import datetime, timedelta, timezone
from html import escape

from flask import Flask
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters

TOKEN = os.getenv("TOKEN")
PORT = int(os.getenv("PORT", "8080"))
TZ = timezone(timedelta(hours=8))

PUBLIC_URL = os.getenv("PUBLIC_URL") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
if PUBLIC_URL and not PUBLIC_URL.startswith("http"):
    PUBLIC_URL = "https://" + PUBLIC_URL

ALERT_USERS = "@HFDG168 @ZHTT16888"

workers = {}
away = {}
late_workers = {}
off_workers = {}
logs = {}

LIMITS = {
    "wc": ("上厕所", 15),
    "cy": ("抽烟", 10),
    "cf": ("吃饭", 35),
    "cq": ("出去", 10),
}

web = Flask(__name__)


def now():
    return datetime.now(TZ)


def get_name(user):
    return user.full_name or user.username or str(user.id)


def safe(text):
    return escape(str(text))


def chat_logs(chat_id):
    logs.setdefault(chat_id, [])
    return logs[chat_id]


def add_log(chat_id, name, action, detail):
    chat_logs(chat_id).append({
        "time": now().strftime("%Y-%m-%d %H:%M:%S"),
        "name": name,
        "action": action,
        "detail": detail,
    })


def get_shift(t):
    if 9 <= t.hour < 21:
        shift = "白班"
        late = t.replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        shift = "夜班"
        late = t.replace(hour=22, minute=0, second=0, microsecond=0)
    return shift, late


def detail_link(chat_id):
    if not PUBLIC_URL:
        return "员工名单：请先设置 PUBLIC_URL"
    return f'员工名单：<a href="{PUBLIC_URL}/report/{chat_id}">详细查看</a>'


def summary(chat_id):
    return f"远程当天上班总人数：{len(workers)}\n{detail_link(chat_id)}"


async def send_msg(message, text):
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


async def alert_group(context, chat_id, text):
    await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True
    )


@web.route("/")
def home():
    return "打卡机器人运行中"


@web.route("/report/<chat_id>")
def report(chat_id):
    try:
        cid = int(chat_id)
    except:
        return "错误的链接"

    worker_lines = []
    for i, info in enumerate(workers.values(), 1):
        worker_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜上班 {info['start'].strftime('%H:%M')}｜{'迟到' if info['late'] else '正常'}"
        )

    away_lines = []
    for i, info in enumerate(away.values(), 1):
        used = int((now() - info["start"]).total_seconds() // 60)
        away_lines.append(
            f"{i}. {safe(info['name'])}｜{info['action']}｜已用 {used} 分钟｜限制 {info['limit']} 分钟"
        )

    late_lines = []
    for i, info in enumerate(late_workers.values(), 1):
        late_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜迟到 {info['minutes']} 分钟"
        )

    off_lines = []
    for i, info in enumerate(off_workers.values(), 1):
        off_lines.append(
            f"{i}. {safe(info['name'])}｜{info['shift']}｜下班 {info['time'].strftime('%H:%M')}｜工作 {info['work']}"
        )

    log_lines = []
    for item in logs.get(cid, []):
        log_lines.append(
            f"{item['time']}｜{safe(item['name'])}｜{item['action']}｜{item['detail']}"
        )

    return f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>打卡详情</title>
        <style>
            body {{ font-family: Arial; padding: 20px; background:#f5f5f5; }}
            .box {{ background:white; padding:15px; margin-bottom:15px; border-radius:10px; }}
            h2 {{ margin-top:0; }}
            pre {{ white-space:pre-wrap; font-size:16px; }}
        </style>
    </head>
    <body>
        <h1>远程打卡详情</h1>

        <div class="box">
            <h2>当前上班人数：{len(workers)}</h2>
            <pre>{chr(10).join(worker_lines) if worker_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>当前离岗人员</h2>
            <pre>{chr(10).join(away_lines) if away_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>今日迟到人员</h2>
            <pre>{chr(10).join(late_lines) if late_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>今日下班人员</h2>
            <pre>{chr(10).join(off_lines) if off_lines else "暂无"}</pre>
        </div>

        <div class="box">
            <h2>全部打卡记录</h2>
            <pre>{chr(10).join(log_lines) if log_lines else "暂无"}</pre>
        </div>
    </body>
    </html>
    """


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_msg(
        update.message,
        "打卡机器人已启动\n\n"
        "上班：sb\n"
        "下班：xb\n"
        "上厕所：wc\n"
        "抽烟：cy\n"
        "吃饭：cf\n"
        "出去：cq\n"
        "回来：1"
    )


async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip().lower()
    user = update.message.from_user
    uid = user.id
    name = get_name(user)
    chat_id = update.message.chat_id
    t = now()

    if text == "sb":
        if uid in workers:
            await send_msg(
                update.message,
                f"⚠️ {safe(name)} 已经上班打卡过了，不能重复打卡。\n\n{summary(chat_id)}"
            )
            await alert_group(
                context,
                chat_id,
                f"⚠️ 打卡异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"类型：重复上班打卡\n"
                f"时间：{t.strftime('%H:%M')}\n\n"
                f"{ALERT_USERS}"
            )
            return

        shift, late_time = get_shift(t)
        is_late = t > late_time
        late_minutes = int((t - late_time).total_seconds() // 60) if is_late else 0

        workers[uid] = {
            "name": name,
            "start": t,
            "shift": shift,
            "late": is_late,
        }

        if is_late:
            late_workers[uid] = {
                "name": name,
                "shift": shift,
                "minutes": late_minutes,
            }

        add_log(chat_id, name, "上班", f"{shift}｜{t.strftime('%H:%M')}｜{'迟到' if is_late else '正常'}")

        msg = (
            f"✅ {safe(name)} 上班打卡成功\n"
            f"班次：{shift}\n"
            f"打卡时间：{t.strftime('%H:%M')}\n"
            f"状态：{'迟到 ' + str(late_minutes) + ' 分钟' if is_late else '正常'}\n\n"
            f"{summary(chat_id)}"
        )
        await send_msg(update.message, msg)

        if is_late:
            await alert_group(
                context,
                chat_id,
                f"⚠️ 上班迟到，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"班次：{shift}\n"
                f"打卡时间：{t.strftime('%H:%M')}\n"
                f"迟到：{late_minutes}分钟\n\n"
                f"{ALERT_USERS}"
            )
        return

    if text == "xb":
        if uid not in workers:
            await send_msg(update.message, f"⚠️ {safe(name)} 你还没有上班打卡。")
            await alert_group(
                context,
                chat_id,
                f"⚠️ 打卡异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"类型：未上班就下班打卡\n"
                f"时间：{t.strftime('%H:%M')}\n\n"
                f"{ALERT_USERS}"
            )
            return

        info = workers.pop(uid)
        away.pop(uid, None)

        work_time = t - info["start"]
        hours = int(work_time.total_seconds() // 3600)
        minutes = int((work_time.total_seconds() % 3600) // 60)

        off_workers[uid] = {
            "name": name,
            "shift": info["shift"],
            "time": t,
            "work": f"{hours}小时{minutes}分钟",
        }

        add_log(chat_id, name, "下班", f"{info['shift']}｜{t.strftime('%H:%M')}｜{hours}小时{minutes}分钟")

        await send_msg(
            update.message,
            f"✅ {safe(name)} 下班打卡成功\n"
            f"班次：{info['shift']}\n"
            f"下班时间：{t.strftime('%H:%M')}\n"
            f"工作时长：{hours}小时{minutes}分钟\n\n"
            f"{summary(chat_id)}"
        )
        return

    if text in LIMITS:
        if uid not in workers:
            await send_msg(update.message, f"⚠️ {safe(name)} 你还没有上班打卡，不能离岗。")
            await alert_group(
                context,
                chat_id,
                f"⚠️ 离岗异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"类型：未上班就申请离岗\n"
                f"操作：{text}\n"
                f"时间：{t.strftime('%H:%M')}\n\n"
                f"{ALERT_USERS}"
            )
            return

        if uid in away:
            old = away[uid]
            await send_msg(update.message, f"⚠️ {safe(name)} 当前已经在{old['action']}，不能重复离岗。")
            await alert_group(
                context,
                chat_id,
                f"⚠️ 离岗异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"类型：重复离岗\n"
                f"当前状态：{old['action']}\n"
                f"新操作：{text}\n"
                f"时间：{t.strftime('%H:%M')}\n\n"
                f"{ALERT_USERS}"
            )
            return

        action, limit = LIMITS[text]

        away[uid] = {
            "name": name,
            "action": action,
            "start": t,
            "limit": limit,
            "chat_id": chat_id,
        }

        add_log(chat_id, name, action, f"开始｜限制 {limit} 分钟")

        await send_msg(
            update.message,
            f"⏳ {safe(name)} 开始{action}\n"
            f"限制时间：{limit}分钟\n"
            f"回来请回复：1\n\n"
            f"{summary(chat_id)}"
        )

        await alert_group(
            context,
            chat_id,
            f"⚠️ 离岗提醒，请关注\n\n"
            f"员工：{safe(name)}\n"
            f"项目：{action}\n"
            f"限制时间：{limit}分钟\n"
            f"开始时间：{t.strftime('%H:%M')}\n\n"
            f"{ALERT_USERS}"
        )

        async def check_timeout(user_id):
            await asyncio.sleep(limit * 60)

            info = away.get(user_id)
            if info:
                used = int((now() - info["start"]).total_seconds() // 60)
                await alert_group(
                    context,
                    info["chat_id"],
                    f"⚠️ 离岗超时，请处理\n\n"
                    f"员工：{safe(info['name'])}\n"
                    f"项目：{info['action']}\n"
                    f"限制时间：{info['limit']}分钟\n"
                    f"当前已用：{used}分钟\n"
                    f"状态：超时未归\n\n"
                    f"{ALERT_USERS}"
                )

        asyncio.create_task(check_timeout(uid))
        return

    if text == "1":
        if uid not in away:
            await send_msg(update.message, f"{safe(name)} 当前没有离岗记录。")
            return

        info = away.pop(uid)
        used = int((t - info["start"]).total_seconds() // 60)
        is_over = used > info["limit"]
        status = "正常" if not is_over else f"超时 {used - info['limit']} 分钟"

        add_log(chat_id, name, f"{info['action']}返回", f"用时 {used} 分钟｜{status}")

        await send_msg(
            update.message,
            f"✅ {safe(name)} 已返回岗位\n"
            f"项目：{info['action']}\n"
            f"用时：{used}分钟\n"
            f"状态：{status}\n\n"
            f"{summary(chat_id)}"
        )

        if is_over:
            await alert_group(
                context,
                chat_id,
                f"⚠️ 离岗返回异常，请处理\n\n"
                f"员工：{safe(name)}\n"
                f"项目：{info['action']}\n"
                f"限制时间：{info['limit']}分钟\n"
                f"实际用时：{used}分钟\n"
                f"超时：{used - info['limit']}分钟\n\n"
                f"{ALERT_USERS}"
            )
        return

    if text in ["rs", "/rs"]:
        await send_msg(update.message, summary(chat_id))
        return


def run_web():
    web.run(host="0.0.0.0", port=PORT)


threading.Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT, handle))
app.run_polling()
