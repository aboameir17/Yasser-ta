# --- المكتبات ---
import logging
import asyncio
import random
import time
import os
import json
import unicodedata
import re
import io
import difflib
import requests
import httpx  
import aiohttp
import arabic_reshaper
import math
import traceback
import numpy as np
import pandas as pd
from aiohttp import web
from scipy.stats import linregress
from scipy.signal import find_peaks
from typing import Dict, Union
from aiogram import types
from datetime import datetime, timedelta # 💡 تمت الإضافة هنا
from aiogram.dispatcher.filters import Text 
from pilmoji import Pilmoji 
from PIL import Image, ImageDraw, ImageFont, ImageOps
from bidi.algorithm import get_display
from aiogram import Bot, Dispatcher, types, executor
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from supabase import create_client, Client

# --- المفاتيح ---
ADMIN_ID = 8695560834
OWNER_USERNAME = ""
#
# سحب التوكينات من Render (لن يعمل البوت بدونها في الإعدادات)
API_TOKEN = os.getenv('BOT_TOKEN')
SUPABASE_URL = os.getenv('SUPABASE_URL')
SUPABASE_KEY = os.getenv('SUPABASE_KEY')
GROUP_ID = os.getenv('GROUP_ID')
tell_1 = os.getenv('tell_1')
tell_2 = os.getenv('tell_2')


# 2. التحقق ثانياً
if not API_TOKEN or not GROUP_ID:
    logging.error("❌ خطأ: المتغيرات المشفرة مفقودة في إعدادات Render!")
    

# تعريف المحركات
bot = Bot(token=API_TOKEN, parse_mode="HTML")
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
# 1. في بداية الملف (خارج كل الدوال) قم بتعريف هذا المتغير
bot_username = None 

# --- القواميس ---
active_investigations = {}
# 1. تخزين بيانات جلسات التداول المؤقتة لكل مستخدم
trade_sessions = {} 

# 2. إدارة مهام التحديث اللحظي (Tasks) لمنع التكرار والحظر
# يجب أن يكون قاموساً (Dictionary) لكي نتمكن من إلغاء المهمة السابقة لكل مستخدم
active_updates = {} 

# 3. إعدادات الرافعة والنسب والمدد (إذا لم تكن معرفة لديك)
LEVERAGE_LEVELS = [1, 5, 10, 20, 50, 75, 100]
MARGIN_PCT_LEVELS = [10, 25, 50, 75, 100]

# --- قسم الدوال ---

async def get_trading_account_snapshot(user_id):
    try:
        user_res = supabase.table("users_global_profile").select("bank_balance").eq("user_id", user_id).execute()
        free_cash = float(user_res.data[0]['bank_balance']) if user_res.data else 0.0
        
        trades = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("is_active", True).execute()
        
        total_used_margin = 0.0
        total_unrealized_pnl = 0.0
        
        for t in trades.data:
            mar = float(t['margin'])
            total_used_margin += mar
            # ... (حساب pnl_pct كما في السابق)
            total_unrealized_pnl += (mar * pnl_pct * float(t['leverage']))

        # 🎯 المنطق الجديد
        total_balance = free_cash + total_used_margin # هذا الـ 1000 في مثالك
        total_equity = total_balance + total_unrealized_pnl # القيمة مع الربح/الخسارة
        
        return {
            "free_cash": round(free_cash, 2),
            "used_margin": round(total_used_margin, 2),
            "total_balance": round(total_balance, 2), # الرصيد الكلي المجموع
            "total_pnl": round(total_unrealized_pnl, 2),
            "total_equity": round(total_equity, 2)
        }
    except Exception as e:
        # التعامل مع الخطأ
        return {"free_cash": 0, "used_margin": 0, "total_balance": 0, "total_pnl": 0, "total_equity": 0}


def generate_candle_chart(direction):
    """تمثيل مرئي بسيط لاتجاه الحركة الحالية"""
    if direction == 'UP':
        return "📉 ⇠ |---🟩---|\n⇠ 🚀 صعود إيجابي"
    else:
        return "📈 ⇠ |---🟥---|\n⇠ 🩸 هبوط سلبي"

import math
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def build_portfolio_view(portfolios, stats, page=0, filter_type="all"):
    # 1. معالجة وتصفية البيانات (تعديل معيار النجاح والفشل إلى 40%)
    filtered = []
    for p in portfolios:
        # معالجة مشكلة الـ 0.0%: إذا كانت النسبة صفر، نقوم بحسابها برمجياً
        pnl = float(p.get('pnl_percentage') or 0)
        if pnl == 0:
            curr = float(p.get('current_balance', 0))
            prev = float(p.get('previous_balance', 0)) # تأكد أن لديك هذا الحقل في القاعدة
            if prev > 0:
                pnl = ((curr - prev) / prev) * 100
                p['pnl_percentage'] = pnl # تحديث القيمة للواجهة

        # الفرز بناءً على الشرط الجديد (40% فما فوق ناجح، أقل من 40% فاشل)
        if filter_type == "win" and pnl >= 40.0: 
            filtered.append(p)
        elif filter_type == "loss" and pnl < 40.0: 
            filtered.append(p)
        elif filter_type == "all": 
            filtered.append(p)
        
    # 2. نظام الصفحات (الباجينيشن)
    items_per_page = 6
    total_pages = math.ceil(len(filtered) / items_per_page) if filtered else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_items = filtered[start_idx:end_idx]

    # 3. بناء النص
    filter_names = {"all": "🌐 الكل", "win": "🟢 الناجحة (≥ 40%)", "loss": "🔴 الفاشلة (< 40%)"}
    text = "💼 <b>لوحة القيادة | المحفظة الاستثمارية</b>\n"
    text += f"🔎 <b>الفلتر الحالي:</b> {filter_names[filter_type]}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not current_items:
        text += "📭 <i>لا توجد محافظ مطابقة لهذا الفلتر...</i>\n"
    else:
        emojis = ["1️⃣", "2️⃣", "3️⃣", "⁦4️⃣⁩", "⁦5️⃣⁩", "⁦6️⃣⁩"]
        for i, p in enumerate(current_items):
            s_name = p['strategy_name']
            s_id = p.get('strategy_id', 'N/A') # إضافة رقم الإستراتيجية
            curr_bal = float(p.get('current_balance', 0))
            pnl_perc = float(p.get('pnl_percentage', 0))
            s_stats = stats.get(s_id, {'wins': 0, 'losses': 0})
            
            # تعديل الأيقونات حسب المعيار الجديد
            icon = "🟢" if pnl_perc >= 40.0 else "🔴"
            sign = "+" if pnl_perc > 0 else ""
            
            # عرض رقم الإستراتيجية بجوار الاسم
            text += f"{emojis[i]} <b>{s_name}</b> <code>[رقم: {s_id}]</code>\n"
            text += f"   💵 <b>الرصيد:</b> <code>{curr_bal:.2f}$</code> | 📈 <b>النمو:</b> <code>{sign}{pnl_perc:.2f}% {icon}</code>\n"
            text += f"   🏆 <b>الناجحة:</b> {s_stats['wins']} | 💔 <b>الفاشلة:</b> {s_stats['losses']}\n"
            text += "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"

    # 4. بناء الأزرار (لوحة التحكم)
    kb = InlineKeyboardMarkup(row_width=3)
    
    # أزرار الإستراتيجيات
    strat_buttons = []
    for i, p in enumerate(current_items):
        strat_buttons.append(InlineKeyboardButton(f"{emojis[i]} التفاصيل", callback_data=f"si:{p['strategy_id']}"))
    if strat_buttons:
        kb.add(*strat_buttons)

    # أزرار الفلترة للمحافظ
    kb.row(
        InlineKeyboardButton("🟢 الناجحة", callback_data=f"p:0:win"),
        InlineKeyboardButton("🌐 الكل", callback_data=f"p:0:all"),
        InlineKeyboardButton("🔴 الفاشلة", callback_data=f"p:0:loss")
    )
    
    # أزرار التنقل والتحديث
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"p:{page-1}:{filter_type}"))
    
    nav_buttons.append(InlineKeyboardButton("🔄 تحديث", callback_data=f"p:{page}:{filter_type}"))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"p:{page+1}:{filter_type}"))
        
    kb.row(*nav_buttons)
    
    return text, kb


def build_trades_view(trades, strategy_id, trade_type="w", page=0):
    items_per_page = 20
    total_pages = math.ceil(len(trades) / items_per_page) if trades else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_trades = trades[start_idx:end_idx]

    type_name = "🟢 الصفقات الناجحة" if trade_type == "w" else "🔴 الصفقات الفاشلة"
    text = f"🗂 <b>سجل الصفقات | {type_name}</b>\n"
    text += f"🔢 <b>صفحة:</b> {page+1}/{total_pages} | <b>إستراتيجية رقم:</b> <code>{strategy_id}</code>\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not current_trades:
        text += "<i>لا توجد صفقات من هذا النوع مسجلة بعد...</i>\n"
    else:
        for i, t in enumerate(current_trades, start_idx + 1):
            coin = t['coin_name']
            pnl = float(t.get('realized_pnl', 0))
            perc = float(t.get('pnl_percentage', 0))
            sign = "+" if pnl > 0 else ""
            text += f"<b>{i}.</b> #{coin} ➔ <code>{sign}{pnl:.2f}$</code> ({sign}{perc:.2f}%)\n"

    # بناء الأزرار
    kb = InlineKeyboardMarkup(row_width=2)
    
    # فلتر الصفقات
    kb.row(
        InlineKeyboardButton("🟢 الناجحة", callback_data=f"tr:{strategy_id}:w:0"),
        InlineKeyboardButton("🔴 الخاسرة", callback_data=f"tr:{strategy_id}:l:0")
    )
    
    # التنقل (التالي والرجوع للصفقات)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"tr:{strategy_id}:{trade_type}:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"tr:{strategy_id}:{trade_type}:{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
        
    # رجوع للوحة القيادة
    kb.add(InlineKeyboardButton("🔙 رجوع للوحة المحافظ", callback_data="p:0:all"))
    return text, kb
    

def build_trades_view(trades, strategy_id, trade_type="w", page=0):
    items_per_page = 5
    total_pages = math.ceil(len(trades) / items_per_page) if trades else 1
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * items_per_page
    end_idx = start_idx + items_per_page
    current_trades = trades[start_idx:end_idx]

    type_name = "🟢 الصفقات الناجحة" if trade_type == "w" else "🔴 الصفقات الفاشلة"
    text = f"🗂 <b>سجل الصفقات | {type_name}</b>\n"
    text += f"🔢 <b>صفحة:</b> {page+1}/{total_pages}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not current_trades:
        text += "<i>لا توجد صفقات من هذا النوع مسجلة بعد...</i>\n"
    else:
        for i, t in enumerate(current_trades, start_idx + 1):
            coin = t['coin_name']
            pnl = float(t.get('realized_pnl', 0))
            perc = float(t.get('pnl_percentage', 0))
            sign = "+" if pnl > 0 else ""
            text += f"<b>{i}.</b> #{coin} ➔ <code>{sign}{pnl:.2f}$</code> ({perc:.2f}%)\n"

    # بناء الأزرار
    kb = InlineKeyboardMarkup(row_width=2)
    
    # فلتر الصفقات
    kb.row(
        InlineKeyboardButton("🟢 الناجحة", callback_data=f"tr:{strategy_id}:w:0"),
        InlineKeyboardButton("🔴 الخاسرة", callback_data=f"tr:{strategy_id}:l:0")
    )
    
    # التنقل
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"tr:{strategy_id}:{trade_type}:{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"tr:{strategy_id}:{trade_type}:{page+1}"))
    if nav_buttons:
        kb.row(*nav_buttons)
        
    # رجوع
    kb.add(InlineKeyboardButton("🔙 رجوع للوحة القيادة", callback_data="p:0:all"))
    return text, kb

import math

async def get_dashboard_data(supabase, user_id):
    """جلب بيانات المحافظ والإحصائيات مرة واحدة لتسريع الأداء"""
    res_port = supabase.table("portfolio").select("*").eq("player_name", str(user_id)).order("current_balance", desc=True).execute()
    portfolios = res_port.data

    res_trades = supabase.table("active_trades").select("strategy_id, realized_pnl").eq("user_id", user_id).eq("status", "مغلقة").execute()
    
    stats = {}
    for t in res_trades.data:
        sid = t.get('strategy_id')
        if not sid: continue
        if sid not in stats: stats[sid] = {'wins': 0, 'losses': 0}
        
        pnl = float(t.get('realized_pnl', 0))
        if pnl > 0: stats[sid]['wins'] += 1
        else: stats[sid]['losses'] += 1

    return portfolios, stats

async def get_strategy_trades(supabase, user_id, strategy_id, trade_type="w"):
    """جلب صفقات إستراتيجية معينة (w=ناجحة, l=خاسرة)"""
    query = supabase.table("active_trades").select("*").eq("user_id", user_id).eq("strategy_id", strategy_id).eq("status", "مغلقة")
    
    if trade_type == "w":
        res = query.gt("realized_pnl", 0).order("realized_pnl", desc=True).execute()
    else:
        res = query.lte("realized_pnl", 0).order("realized_pnl", desc=False).execute()
        
    return res.data
# --- قسم دوال الكيبورد ---
# ==========================================
# 3. قوالب واجهات المستخدم (Secured Keyboards)
# ==========================================

def get_market_keyboard(user_id):
    markup = InlineKeyboardMarkup(row_width=3)
    
    # تصحيح: إضافة الفواصل بين الأزرار وحذف المراجع النصية التي تسبب الخطأ
    markup.row(
        InlineKeyboardButton("🔥 الرائجة", callback_data=f"market_tab:{user_id}:trending"),
        InlineKeyboardButton("📈 الرابحة", callback_data=f"market_tab:{user_id}:gainers"),
        InlineKeyboardButton("📉 الخاسرة", callback_data=f"market_tab:{user_id}:losers")
    )
    
    # إضافة الأزرار الرئيسية في صفوف منفصلة
    markup.add(InlineKeyboardButton("🏦 محفظتي الماليـة", callback_data=f"wallet_view:{user_id}"))
    markup.add(InlineKeyboardButton("📋 صفقاتي المفتوحة", callback_data=f"active_trades_view:{user_id}"))

    return markup
  
 # ==========================================
# 3. قوالب واجهات المستخدم المصححة
# ==========================================
async def is_authorized(callback_query: types.CallbackQuery):
    """🛡️ الحارس الشخصي للتأكد من ملكية الأزرار"""
    data_parts = callback_query.data.split(':')
    if len(data_parts) > 1 and data_parts[1].isdigit():
        owner_id = int(data_parts[1])
        if callback_query.from_user.id != owner_id:
            await callback_query.answer("🚫 هذي ليست محفظتك! العب بعيد يا مبعسس 🤫", show_alert=True)
            return False
    return True

# ==========================================
# 3. قوالب واجهات المستخدم
# ==========================================

# --- [ 1. دالة الكيبورد التفاعلي للفريمات ] ---
def get_coin_keyboard(user_id, symbol, current_tf="15m"):
    markup = InlineKeyboardMarkup(row_width=5)
    
    # صف الفريمات (تحديد الفريم النشط)
    tfs = ['15m', '1h', '2h', '4h', '1d']
    tf_buttons = []
    for tf in tfs:
        text = f"🔘 {tf}" if tf == current_tf else tf
        tf_buttons.append(InlineKeyboardButton(text, callback_data=f"coin_view:{user_id}:{symbol}:{tf}"))
    markup.row(*tf_buttons)
    
    # صف توصية VIP
    markup.row(InlineKeyboardButton("💎 تـوصـيـة VIP حـصـريـة 💎", callback_data=f"vip_signal:{user_id}:{symbol}"))
    
    # صف الأوامر السريعة
    markup.row(
        InlineKeyboardButton("🟢 شـراء (LONG)", callback_data=f"setup_trade:{user_id}:{symbol}:LONG"),
        InlineKeyboardButton("🔴 بـيـع (SHORT)", callback_data=f"setup_trade:{user_id}:{symbol}:SHORT")
    )
    
    # زر الرجوع المخصص
    markup.row(InlineKeyboardButton("🔙 رجـوع", callback_data=f"market_tab:{user_id}:trending"))
    return markup

def get_trade_setup_keyboard(user_id):
    session = trade_sessions.get(user_id)
    if not session: return None
    
    sym = session['symbol']
    side = session['side']
    show_zones = session.get('show_zones', False) # هل عرضنا مناطق الدخول؟
    selected_price = session.get('selected_entry_price', None)

    markup = InlineKeyboardMarkup(row_width=3)
    
    # صف الرافعة والنسبة
    markup.row(
        InlineKeyboardButton(f"⚖️ {session['leverage']}x", callback_data=f"trade_cycle:{user_id}:leverage"),
        InlineKeyboardButton(f"💼 {session['margin_pct']}%", callback_data=f"trade_cycle:{user_id}:margin")
    )
    
    # زر مناطق الدخول (يتحول عند الضغط)
    if not show_zones:
        markup.add(InlineKeyboardButton("🎯 تحديد منطقة الدخول", callback_data=f"trade_zones:{user_id}:show"))
    else:
        # توليد مناطق الدخول
        c_price = session['market_price']
        high = session['high_24h']
        low = session['low_24h']        
        # داخل الكيبورد استبدل سطر zones بـ:    
        zones = []
        if side == 'LONG':
            # مناطق بين الأدنى والسعر الحالي
            zones = get_zones(low, c_price)
        else:
            # مناطق بين الحالي والأعلى
            zones = get_zones(c_price, high)
        
        zone_buttons = []
        for z in zones:
            is_sel = "✅" if selected_price and abs(selected_price - z) < 0.0001 else ""
            txt = f"{is_sel} {z:,.4f}" if z < 1 else f"{is_sel} {z:,.2f}"
            zone_buttons.append(InlineKeyboardButton(txt, callback_data=f"set_zone:{user_id}:{z}"))
        
        markup.row(*zone_buttons[:2])
        markup.row(*zone_buttons[2:])
        markup.add(InlineKeyboardButton("⚡ العودة للسعر المباشر (Market)", callback_data=f"set_zone:{user_id}:market"))

    # زر التأكيد والإلغاء
    confirm_text = "🚀 تأكيد الشراء" if side == 'LONG' else "🩸 تأكيد البيع"
    markup.add(InlineKeyboardButton(confirm_text, callback_data=f"trade_confirm:{user_id}:{sym}"))
    markup.add(InlineKeyboardButton("❌ إلغاء", callback_data=f"coin_view:{user_id}:{sym}"))
    
    return markup
    

async def update_trade_ui(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    if user_id not in trade_sessions: return
    
    # --- 🛡️ نظام منع التكرار والحظر ---
    # إذا كانت هناك حلقة تعمل بالفعل لهذا المستخدم، نقوم بإلغائها لبدء واحدة جديدة بالقيم الجديدة
    if user_id in active_updates:
        active_updates[user_id].cancel()
    
    # إنشاء مهمة (Task) جديدة للحلقة وحفظها في القاموس
    task = asyncio.create_task(run_ui_loop(callback_query, user_id))
    active_updates[user_id] = task

async def run_ui_loop(callback_query, user_id):
    """هذه الدالة الفرعية هي التي تدير الحلقة لمنع تداخل الكود"""
    try:
        for _ in range(15):
            if user_id not in trade_sessions: break
            
            session = trade_sessions[user_id]
            sym = session['symbol']
            
            # 1. جلب السعر اللحظي
            res = supabase.table("crypto_market_simulation").select("*").eq("symbol", sym).execute()
            if not res.data: break
            
            market_price = float(res.data[0]['current_price'])
            session['market_price'] = market_price
            
            # 2. تحديد نوع السعر (Market vs Limit)
            is_limit = session.get('selected_entry_price') is not None
            price = session['selected_entry_price'] if is_limit else market_price
            session['entry_price'] = price
            
            status_tag = "🕒 سـعر معلق (Limit)" if is_limit else "⚡ سـعر الـسوق (مباشر)"
            icon = "📌" if is_limit else "🔄"

            # 3. الحسابات المالية
            margin_amount = session['balance'] * (session['margin_pct'] / 100.0)
            quantity = (margin_amount * session['leverage']) / price
            liq_price = calculate_liquidation(price, session['leverage'], session['side'], margin_amount, quantity)
            
            # 4. بناء النص
            text = (
                f"⚙️ | <b>إعـداد صـفـقـة: #{sym}</b>\n"
                f"الـنوع: {'🟢 LONG' if session['side'] == 'LONG' else '🔴 SHORT'} | {status_tag}\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"💵 سـعـر الـدخول: <code>{price:,.4f} $</code> {icon}\n"
                f"⚖️ الـرافـعـة: <b>{session['leverage']}x</b>\n"
                f"💼 الـمبلغ: <b>{margin_amount:,.2f} $</b> ({session['margin_pct']}%)\n"
                f"⚠️ الـتصفية: <code>{liq_price:,.4f} $</code>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"<i>البيانات تتحدث تلقائياً..</i>"
            )

            try:
                await callback_query.message.edit_text(
                    text, 
                    reply_markup=get_trade_setup_keyboard(user_id), 
                    parse_mode="HTML"
                )
            except Exception: pass

            # 🛑 إذا كان السعر معلقاً، نكتفي بتحديث واحد فقط وننهي الحلقة فوراً
            if is_limit: break
            
            await asyncio.sleep(4) # وقت أمان لمنع حظر تليجرام
            
    except asyncio.CancelledError:
        pass # تم إلغاء المهمة لبدء واحدة جديدة
    finally:
        # مسح المهمة من السجل عند الانتهاء
        if active_updates.get(user_id) == asyncio.current_task():
            active_updates.pop(user_id, None)
            
            
def get_wallet_keyboard(user_id, debt):
    markup = InlineKeyboardMarkup(row_width=2)
    
    # صف الإيداع والسحب
    markup.row(
        InlineKeyboardButton("📥 إيداع للتداول", callback_data=f"transfer_flow:{user_id}:to_bank"),
        InlineKeyboardButton("📤 سحب للمحفظة", callback_data=f"transfer_flow:{user_id}:to_wallet")
    )
    
    # زر القرض أو التسديد
    if debt > 0:
        # إذا كان عليه دين، يظهر زر التسديد باللون الأحمر (إيموجي)
        markup.add(InlineKeyboardButton("🔴 تسديد القرض المستحق", callback_data=f"repay_loan:{user_id}"))
    else:
        # إذا كان سليم، يظهر زر طلب القرض
        markup.add(InlineKeyboardButton("💰 طلب قرض سريع", callback_data=f"loan_menu:{user_id}"))
        
    # صف السوق والصفقات
    markup.row(
        # تم حذف الشرطة السفلية _ قبل النقطتين : لتطابق المعالج
        InlineKeyboardButton("📋 صفقاتي", callback_data=f"active_trades_view:{user_id}"),
        InlineKeyboardButton("🛒 السوق", callback_data=f"market_tab:{user_id}:trending")
    )
    return markup
    

def get_trades_keyboard(user_id, trades):
    markup = InlineKeyboardMarkup(row_width=1) 
    for trade in trades:
        # تحويل المعرف لسلسلة نصية
        t_id_str = str(trade.get('trade_id'))
        symbol = trade.get('symbol', 'COIN')
        
        # 1. زر إعدادات الصفقة (للتعديل على SL/TP)
        # 2. زر عرض الشارت (ينقله لواجهة التحليل coin_view)
        markup.row(
            InlineKeyboardButton(f"⚙️ إعدادات {symbol}", callback_data=f"manage_trade:{t_id_str}"),
            InlineKeyboardButton(f"📊 عرض الشارت", callback_data=f"coin_view:{user_id}:{symbol}")
        )        
        
    # أزرار التنقل الإضافية
    markup.add(InlineKeyboardButton("⏳ الطلبات المعلقة", callback_data=f"pending_trades_view:{user_id}"))
    markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{user_id}:trending"))
    return markup
    

# --- قسم الكلاس ---
class BankTransfer(StatesGroup):
    waiting_for_amount = State()      # انتظار مبلغ التحويل/الإيداع
    waiting_for_account = State()     # انتظار رقم الحساب (في حال التحويل لشخص)

# ==========================================
# 6. معالج أمر البدء المطور في الخاص /start
# ==========================================
# --- معالج أمر البدء المطور في الخاص /start ---
@dp.message_handler(commands=['start'], chat_type=types.ChatType.PRIVATE)
async def private_start_handler(message: types.Message):
    user_id = message.from_user.id
    first_name = message.from_user.first_name
    last_name = message.from_user.last_name or ""
    username = f"@{message.from_user.username}" if message.from_user.username else "بدون معرف"
    full_name = f"{first_name} {last_name}".strip()
    
    # ---------------------------------------------------------
    # 🚨 [ نظام إنذار المطور: إرسال إشعار للمجموعة بدخول شخص جديد ]
    # ---------------------------------------------------------
    try:
        # تأكد أن المتغير GROUP_ID مسحوب بشكل صحيح في بداية ملفك
        if GROUP_ID: 
            # إنشاء رابط يفتح بروفايل الشخص بمجرد الضغط على اسمه
            user_profile_link = f"<a href='tg://user?id={user_id}'>{full_name}</a>"
            
            alert_msg = (
                f"🚨 <b>رادار البوت: مستخدم جديد!</b>\n\n"
                f"👤 <b>الاسم:</b> {user_profile_link}\n"
                f"🔗 <b>المعرف:</b> {username}\n"
                f"🆔 <b>الآيدي:</b> <code>{user_id}</code>"
            )
            # إرسال الإشعار للمجموعة
            await bot.send_message(chat_id=GROUP_ID, text=alert_msg, parse_mode="HTML")
    except Exception as e:
        import logging
        logging.error(f"❌ خطأ في إرسال إشعار دخول المستخدم للمجموعة: {e}")

    # ---------------------------------------------------------
    # 📲 [ لوحة الأزرار ورسالة الترحيب للمستخدم ]
    # ---------------------------------------------------------
    kb_start = InlineKeyboardMarkup(row_width=2)
    kb_start.add(
        InlineKeyboardButton("💻 تواصل مع المطور", url="https://t.me/al3bet"),
        InlineKeyboardButton("📢 قناة البوت", url="https://t.me/log_463") # لا تنسَ تعديل رابط القناة هنا
    )

    # تحسين التنسيق ليكون أكثر احترافية وفخامة
    welcome_msg = (
        f"👋 <b>أهلاً بك يا {first_name} في أعظم نظام تداول في سوق العملات الرقمية!</b> 🚀\n\n"
        f"يتفوق هذا النظام على البنوك، صناديق التحوط، والمواقع المدفوعة بمراحل؛ بل هي مجرد ألعاب أطفال مقارنةً بالمنطق الجبار الذي يحتويه.\n\n"
        f"👁️‍🗨️ <b>ماذا يقدم لك النظام؟</b>\n"
        f"• كاشف متقدم للسوق، الخديعة، المصائد، وتلاعبات الحيتان.\n"
        f"• أسرار وخفايا حصرية لا تُدرّس حتى في الجامعات.\n"
        f"• نظام إنذار استباقي قبل وقوع الأحداث بمليون مرة .\n"
        f"• نظام إجراء صفقات آلي كل ما عليك هو ربط حسابك بالنظام وهو يقوم بالتداول بدلاً عنك واكثر أمانا بنسبة 100.\n"
        f"• درع أمان متكامل لحمايتك من فوضى وتقلبات السوق ضمان لو خسرت تتعوض والخسارة عندنا مستحيلة.\n\n"
        f"💳 <b> تفاصيل أسعار الباقات بالدولار:</b>\n"
        f"▫️ أسبوع: <b>250$</b>\n"
        f"▫️ شهر: <b>1000$</b>\n"
        f"▫️ 3 أشهر: <b>2500$</b>\n"
        f"▫️ 6 أشهر: <b>4000$</b>\n"
        f"▫️ سنة كاملة: <b>6000$</b>\n\n"
        f"<i>🤍 ملاحظة: جميع أموال الاشتراكات تذهب لدعم الفقراء واليتامى ابتغاء وجه الله تعالى اما انا مكتفي بما علمني ربي واعطاني من فضله.</i>\n\n"
        f"💬 <b>للتواصل المباشر مع المطور، طلب الاشتراك، أو الإبلاغ عن خلل فني، يرجى استخدام الأزرار أدناه.</b>\n"
        f"نتمنى لكم التوفيق والنجاح الدائم اكتشف اسرار مخفية عنك وكن مليونير."
    )
    
    try:
        # Photo ID الخاص بصورة الترحيب (يفضل صورة فخمة للبوت)
        bot_photo = "AgACAgQAAxkBAA..." 
        await message.answer_photo(
            photo=bot_photo,
            caption=welcome_msg,
            reply_markup=kb_start,
            parse_mode="HTML"
        )
    except Exception:
        # في حال كانت الصورة غير صالحة، يرسل النص فقط
        await message.answer(welcome_msg, reply_markup=kb_start, parse_mode="HTML")


# --- قسم @dp.message_handler المستمعات السمعية *---

@dp.message_handler(commands=['analytics', 'reports'], chat_type=types.ChatType.PRIVATE)
@dp.message_handler(Text(equals=["التحليلات", "التقارير", "النتائج", "الاحصائيات", "مركز القيادة"], ignore_case=True), chat_type=types.ChatType.PRIVATE, state="*")
async def analytics_dashboard_handler(message: types.Message):
    if message.from_user.id != int(ADMIN_ID): return

    kb_analytics = InlineKeyboardMarkup(row_width=2)
    kb_analytics.add(
        InlineKeyboardButton("✅ الإشارات الناجحة", callback_data="report_list:success:0"),
        InlineKeyboardButton("❌ الإشارات الفاشلة", callback_data="report_list:failed:0")
    )
    kb_analytics.add(InlineKeyboardButton("👑 أسرار النجاح (الأكثر تكراراً)", callback_data="report_secrets"))
    
    text = (
        "📊 <b>مركز القيادة والتحليل المتقدم (Backtesting)</b>\n\n"
        "من هنا يمكنك الاطلاع على عصارة قاعدة البيانات لمعرفة ما الذي يعمل في السوق وما الذي يخسر.\n\n"
        "👇 <b>اختر التقرير المطلوب:</b>"
    )
    await message.answer(text, reply_markup=kb_analytics, parse_mode="HTML")

# 1. أمر استدعاء المحفظة لأول مرة
@dp.message_handler(Text(equals=["محفظتي", "حسابي", "المحفظة"], ignore_case=True), state="*")
async def cmd_portfolio(message: types.Message):
    user_id = message.from_user.id
    portfolios, stats = await get_dashboard_data(supabase, user_id)
    text, kb = build_portfolio_view(portfolios, stats, page=0, filter_type="all")
    await message.answer(text, reply_markup=kb, parse_mode="HTML")

# 2. هاندلر لوحة القيادة (تحديث، التالي، السابق، فلترة المحافظ)
# الصيغة: p:{page}:{filter_type}
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('p:'), state="*")
async def cq_portfolio_dashboard(callback_query: types.CallbackQuery):
    _, page_str, filter_type = callback_query.data.split(':')
    user_id = callback_query.from_user.id
    
    portfolios, stats = await get_dashboard_data(supabase, user_id)
    text, kb = build_portfolio_view(portfolios, stats, page=int(page_str), filter_type=filter_type)
    
    # تجنب خطأ "الرسالة لم تتغير" إذا ضغط المستخدم تحديث ولم يتغير شيء
    try:
        await callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback_query.answer("🔄 تم تحديث اللوحة!")
    except:
        await callback_query.answer("✅ البيانات محدثة بالفعل.")

# 3. هاندلر الضغط على زر "التفاصيل" لإستراتيجية معينة (الدخول لصفقاتها)
# الصيغة: si:{strategy_id} -> افتراضياً نعرض الصفقات الناجحة الصفحة 0
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('si:'), state="*")
async def cq_strategy_details(callback_query: types.CallbackQuery):
    strategy_id = int(callback_query.data.split(':')[1])
    user_id = callback_query.from_user.id
    
    # نجلب الصفقات الناجحة كبداية
    trades = await get_strategy_trades(supabase, user_id, strategy_id, trade_type="w")
    text, kb = build_trades_view(trades, strategy_id, trade_type="w", page=0)
    
    await callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback_query.answer()

# 4. هاندلر تصفح الصفقات (التالي، السابق، ناجحة، خاسرة)
# الصيغة: tr:{strategy_id}:{trade_type}:{page}
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('tr:'), state="*")
async def cq_trades_pagination(callback_query: types.CallbackQuery):
    _, strategy_id, trade_type, page_str = callback_query.data.split(':')
    user_id = callback_query.from_user.id
    
    trades = await get_strategy_trades(supabase, user_id, int(strategy_id), trade_type=trade_type)
    text, kb = build_trades_view(trades, int(strategy_id), trade_type=trade_type, page=int(page_str))
    
    try:
        await callback_query.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
        await callback_query.answer()
    except:
        await callback_query.answer()

# ==========================================
# --- [ مستمع السوق ] ---
# ==========================================            
@dp.message_handler(Text(equals=["تداول", "السوق", "التداول"], ignore_case=True))
async def listener_market(message: types.Message):
    user_id = message.from_user.id
    
    # جلب العملات من السوق (Binance Mode)
    res = supabase.table("crypto_market_simulation").select("*").order("volume_24h", desc=True).limit(0).execute()
    coins = res.data
    
    text = "📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n"
    text += "━━━━━━━━━━━━━━━━━━\n"
    text += "🔥 <b>الأكثر رواجاً حالياً:</b>\n\n"
    
    markup = get_market_keyboard(user_id)
    
    if not coins:
        text += "⚠️ لا توجد بيانات في السوق حالياً."
    else:
        for c in coins:
            sym = c['symbol']
            price = float(c['current_price'])
            chg = float(c['change_24h'])
            icon = "🟢" if chg >= 0 else "🔴"
            text += f"{icon} <b>{sym}</b> : <code>{price:,.2f} $</code> ({chg:+.2f}%)\n"
            # إضافة أزرار العملات تحت الرسالة
            markup.add(InlineKeyboardButton(f"عرض {sym} 🪙", callback_data=f"coin_view:{user_id}:{sym}"))

    await message.answer(text, reply_markup=markup, parse_mode="HTML")

# دالة لتنسيق الأرقام لتعرض كاملة بدون أصفار زائدة وبدون صيغة علمية
def format_num(num, decimals=8):
    if num is None: return "0"
    return f"{float(num):.{decimals}f}".rstrip('0').rstrip('.') if '.' in f"{float(num):.{decimals}f}" else f"{float(num):.{decimals}f}"

# دالة جلب الصفقات من قاعدة البيانات بناءً على الفلتر
async def fetch_filtered_trades(supabase, user_id, filter_type="active_profit", page=0, limit=10):
    offset = page * limit
    query = supabase.table("active_trades").select("*").eq("user_id", user_id)

    if filter_type == "active_profit":
        query = query.eq("status", "نشطة").order("pnl_percentage", desc=True)
    elif filter_type == "active_loss":
        query = query.eq("status", "نشطة").order("pnl_percentage", desc=False)
    elif filter_type == "closed_win":
        query = query.eq("status", "مغلقة").gt("realized_pnl", 0).order("closed_at", desc=True)
    elif filter_type == "closed_loss":
        query = query.eq("status", "مغلقة").lte("realized_pnl", 0).order("closed_at", desc=True)

    # تنفيذ الاستعلام مع نظام الصفحات
    res = query.range(offset, offset + limit - 1).execute()
    
    # التحقق من وجود صفحة تالية
    next_page_check = query.range(offset + limit, offset + limit).execute()
    has_next = len(next_page_check.data) > 0

    return res.data, has_next

def get_trades_keyboard(trades, filter_type, page, has_next):
    keyboard = InlineKeyboardMarkup(row_width=2) # جعلناها 2 لتكون الأزرار بجانب بعضها إن أردت
    
    # 1. أزرار الصفقات (باسم العملة والرمز فقط لأن التفاصيل موجودة في النص أعلاه)
    for trade in trades:
        trade_id = trade['id']
        coin = trade['coin_name']
        
        # تحديد لون الزر (إيموجي) بناءً على نوع الصفقة
        icon = "🟢" if trade['trade_type'].upper() in ["LONG", "شراء"] else "🔴"
        
        # اسم الزر: لون + اسم العملة
        btn_text = f"{icon} #{coin}"
        keyboard.add(InlineKeyboardButton(text=btn_text, callback_data=f"view_trade:{trade_id}"))

    # 2. أزرار التنقل (السابق / التالي)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(text="⏪ السابق", callback_data=f"nav:{filter_type}:{page-1}"))
    if has_next:
        nav_buttons.append(InlineKeyboardButton(text="التالي ⏩", callback_data=f"nav:{filter_type}:{page+1}"))
    if nav_buttons:
        keyboard.row(*nav_buttons)

    # 3. أزرار الفلترة الأساسية
    keyboard.row(
        InlineKeyboardButton(text="📈 نشطة (ربح)", callback_data="nav:active_profit:0"),
        InlineKeyboardButton(text="📉 نشطة (خسارة)", callback_data="nav:active_loss:0")
    )
    keyboard.row(
        InlineKeyboardButton(text="✅ مغلقة (ناجحة)", callback_data="nav:closed_win:0"),
        InlineKeyboardButton(text="❌ مغلقة (فاشلة)", callback_data="nav:closed_loss:0")
    )
    
    return keyboard


# ---------------- دالة مساعدة لبناء قالب رسالة الصفقات (فخم ومميز) ----------------
async def build_trades_list_text(trades, filter_type, page):
    filter_names = {
        "active_profit": "🔥 نشطة (الأكثر ربحاً)",
        "active_loss": "🩸 نشطة (الأكثر خسارة)",
        "closed_win": "🏆 مغلقة (ناجحة)",
        "closed_loss": "💔 مغلقة (فاشلة)"
    }
    
    # ترويسة فخمة
    text = f"🎛 <b>لوحة القيادة | إدارة الصفقات</b>\n"
    text += f"⚜️ <b>التصنيف الحالي:</b> {filter_names.get(filter_type, 'غير محدد')}\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"
    
    if not trades:
        text += "📭 <i>لا توجد صفقات في هذا القسم حالياً...</i>\n"
        return text
        
    # حلقة التكرار لطباعة تفاصيل الصفقات
    for t in trades:
        is_long = t['trade_type'].upper() in ["LONG", "شراء"]
        icon = "🟢" if is_long else "🔴"
        trade_type = "LONG" if is_long else "SHORT"
        s_name = t.get('strategy_used_name') or "غير محدد"
        s_id = t.get('strategy_id', 'غير محدد')
        
        # جلب القيم كأرقام لتجنب أخطاء العمليات الحسابية
        try:
            pnl_perc = float(t.get('pnl_percentage', 0.0))
            used_amount = float(t.get('used_amount', 0.0))
        except (ValueError, TypeError):
            pnl_perc = 0.0
            used_amount = 0.0
            
        # حساب الربح/الخسارة بالدولار
        pnl_usd = used_amount * (pnl_perc / 100)
        
        # تنسيق إشارة الموجب لتمييز الأرباح
        sign_usd = "+" if pnl_usd > 0 else ""
        sign_perc = "+" if pnl_perc > 0 else ""
        
        text += f"{icon} <b>العملة:</b> #{t['coin_name']} | <b>النوع:</b> <code>{trade_type}</code>\n"
        text += f"🎯 <b>الإستراتيجية:</b> {s_name} <code>[رقم: {s_id}]</code>\n"
        text += f"💵 <b>المبلغ المستخدم:</b> <code>{used_amount:.2f}$</code>\n"
        text += f"📊 <b>نسبة العائد:</b> <code>{sign_perc}{pnl_perc:.2f}%</code>\n"
        text += f"💸 <b>الربح/الخسارة:</b> <code>{sign_usd}{pnl_usd:.2f}$</code>\n"
        text += "┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈\n"
        
    # تذييل الرسالة
    text += f"\n📄 <b>رقم الصفحة:</b> <code>{page + 1}</code>\n"
    text += "👇 <i>انقر على أزرار العملات بالأسفل لعرض التفاصيل الدقيقة:</i>"
    
    return text


# ---------------- هاندلر استدعاء قائمة الصفقات ----------------
@dp.message_handler(Text(equals=["صفقاتي", "الصفقات"], ignore_case=True), state="*")
async def listener_trades(message: types.Message):
    user_id = int(message.from_user.id)
    try:
        filter_type = "active_profit"
        page = 0
        trades, has_next = await fetch_filtered_trades(supabase, user_id, filter_type, page)

        if not trades:
            await message.answer("⚠️ لا توجد صفقات لعرضها حالياً في هذا القسم.", reply_markup=get_market_keyboard(user_id))
            return

        # استدعاء الدالة المساعدة لبناء النص الفخم
        text = await build_trades_list_text(trades, filter_type, page)

        await message.answer(text, reply_markup=get_trades_keyboard(trades, filter_type, page, has_next), parse_mode="HTML")
    except Exception as e:
        logging.error(f"Listener Error: {e}")
        await message.answer("⚠️ عذراً، حدث خطأ أثناء جلب صفقاتك.")
        

# ---------------- هاندلر التنقل بين الصفحات وتغيير الفلاتر ----------------
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('nav:'), state="*")
async def navigate_trades_callback(callback_query: types.CallbackQuery):
    user_id = callback_query.from_user.id
    _, filter_type, page_str = callback_query.data.split(':')
    page = int(page_str)

    try:
        trades, has_next = await fetch_filtered_trades(supabase, user_id, filter_type, page)
        
        # استدعاء نفس الدالة المساعدة لضمان توحيد القالب عند التنقل
        text = await build_trades_list_text(trades, filter_type, page)
        keyboard = get_trades_keyboard(trades, filter_type, page, has_next)
        
        # تحديث الرسالة فقط إذا كان هناك تغيير (لتجنب أخطاء التليجرام)
        await callback_query.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Navigation Error: {e}")
        await callback_query.answer("⚠️ حدث خطأ أثناء تحديث القائمة.", show_alert=True)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('view_trade:'), state="*")
async def view_trade_details(callback_query: types.CallbackQuery):
    trade_id = int(callback_query.data.split(':')[1])
    
    try:
        res = supabase.table("active_trades").select("*").eq("id", trade_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ لم يتم العثور على الصفقة، ربما تم حذفها.", show_alert=True)
            
        trade = res.data[0]
        
        # تجهيز المتغيرات المشتركة
        is_long = trade['trade_type'].upper() in ["LONG", "شراء"]
        trade_icon = "🟢" if is_long else "🔴"
        trade_label = "شراء (LONG)" if is_long else "بيع (SHORT)"
        strategy_name = trade.get('strategy_used_name') or "غير محدد"
        strategy_id = trade.get('strategy_id', 'غير محدد')
        coin_name = trade['coin_name']
        leverage = trade['leverage']
        coin_shares = trade['coin_shares']
        used_amount = trade['used_amount']
        borrowed_amount = trade['borrowed_amount']
        entry_price = trade['entry_price']
        current_price = trade['current_price']
        
        # السعر العادل (إذا لم يكن في قاعدة البيانات يمكنك جلبه من API، هنا افترضنا أنه موجود أو يطابق الحالي)
        fair_price = trade.get('fair_price', current_price) 
        
        highest = trade.get('highest_price_reached') or entry_price
        lowest = trade.get('lowest_price_reached') or entry_price
        pnl_percentage = trade.get('pnl_percentage', 0.0)
        
        # ----------------- حساب الوقت المستغرق ووقت الفتح -----------------
        created_at_str = trade.get('created_at')
        time_spent_str = "غير معروف"
        open_time_str = "غير معروف"
        
        if created_at_str:
            try:
                # تحويل النص إلى كائن وقت وتوحيد المنطقة الزمنية إلى UTC
                created_dt = datetime.fromisoformat(created_at_str.replace('Z', '+00:00'))
                if created_dt.tzinfo is None: 
                    created_dt = created_dt.replace(tzinfo=timezone.utc)
                
                # استخراج وقت فتح الصفقة الفعلي
                open_time_str = created_dt.strftime("%Y-%m-%d %H:%M")
                
                # تحديد وقت النهاية بناءً على حالة الصفقة
                if trade['status'] == "نشطة":
                    end_time = datetime.now(timezone.utc)
                else:
                    end_time = datetime.fromisoformat(trade['closed_at'].replace('Z', '+00:00')) if trade.get('closed_at') else datetime.now(timezone.utc)
                
                # حساب الفارق الزمني
                time_spent = end_time - created_dt
                total_seconds = int(time_spent.total_seconds()) # استخدام إجمالي الثواني لتجنب أخطاء الأيام
                
                if total_seconds < 60:
                    time_spent_str = "أقل من دقيقة"
                else:
                    days = total_seconds // 86400
                    hours = (total_seconds % 86400) // 3600
                    minutes = (total_seconds % 3600) // 60
                    
                    time_parts = []
                    if days > 0: time_parts.append(f"{days} يوم")
                    if hours > 0: time_parts.append(f"{hours} ساعة")
                    if minutes > 0: time_parts.append(f"{minutes} دقيقة")
                    time_spent_str = " و ".join(time_parts)
            except Exception as e:
                logging.error(f"Time parsing error: {e}")
                time_spent_str = "خطأ في حساب الوقت"

        if trade['status'] == "نشطة":
            # ----------------- قالب الصفقة النشطة -----------------
            support_zone = trade.get('support_zone', 0)
            stop_loss = trade.get('stop_loss', 0)
            target_1 = trade.get('target_1', 0)
            target_2 = trade.get('target_2', 0)
            target_3 = trade.get('target_3', 0)
            net_pnl = float(used_amount) * (float(pnl_percentage) / 100)

            notification_msg = (
                "ــــــــــــــــــــــــــــــــــــ\n\n"
                f"{trade_icon} نوع الصفقة : {trade_label}\n"
                f"🎯 إسم الإستراتيجية : {strategy_name}\n"
                f"🔢 رقم الاستراتجية : {strategy_id}\n"
                f"💸 إسم العملة : #{coin_name}\n"
                f"🔄 الرافعة المالية : {leverage}x\n"
                f"💳 الكمية : {format_num(coin_shares, 4)}\n"
                f"📊 المبلغ : {format_num(used_amount, 2)}$\n"
                f"🧾 الإقتراض : {format_num(borrowed_amount, 2)}$\n"
                f"📈 سعر الدخول : {format_num(entry_price)}\n"
                f"💲 السعر الحالي : {format_num(current_price)}\n"
                f"⚖️ السعر العادل : {format_num(fair_price)}\n"
                f"⁦🔼 منطقة الدعم : {format_num(support_zone)}\n"
                f"🚫 وقف الخسارة : {format_num(stop_loss)}\n"
                f"🥇 الهدف الاول : {format_num(target_1)}\n"
                f"🥈 الهدف الثاني : {format_num(target_2)}\n"
                f"🥉 الهدف الثالث : {format_num(target_3)}\n"
                f"🔼 اقصى سعر : {format_num(highest)}\n"
                f"🔽 أدنى سعر : {format_num(lowest)}\n"
                f"💵 الربح او الخسارة : {format_num(net_pnl, 4)}$\n"
                f"🧾 النسبة المئوية : {format_num(pnl_percentage, 2)}%\n"
                f"🕛 الوقت المستغرق : {time_spent_str}\n"
                f"📅 وقت فتح الصفقة: {open_time_str}\n\n"
                "ــــــــــــــــــــــــــــــــــــ"
            )
        else:
            # ----------------- قالب الصفقة المغلقة -----------------
            close_price = trade.get('close_price', current_price)
            close_reason = trade.get('close_reason', 'مجهول')
            realized_pnl = trade.get('realized_pnl', 0.0)
            closing_fee = trade.get('trading_fees', 0.0)
            # نفترض جلب رصيد المحفظة من مكان ما، أو نضع 0 إذا لم يكن متوفراً
            updated_balance = 0.0 

            notification_msg = "\n".join([
                "ــــــــــــــــــــــــــــــــــــ",
                "",
                f"{trade_icon} نوع الصفقة : {trade_label}",
                f"🎯 إسم الإستراتيجية : {strategy_name}",
                f"🔢 رقم الاستراتجية : {strategy_id}",
                f"💸 إسم العملة : #{coin_name}",
                f"🔄 الرافعة المالية : {leverage}x",
                f"💳 الكمية : {format_num(coin_shares, 4)}",
                f"📊 المبلغ : {format_num(used_amount, 2)}$",
                f"🧾 الإقتراض : {format_num(borrowed_amount, 2)}$",
                f"📈 سعر الدخول : {format_num(entry_price)}",
                f"⁦📝 سعر الإغلاق : {format_num(close_price)}",
                f"🔼 اقصى سعر : {format_num(highest)}",
                f"🔽 أدنى سعر : {format_num(lowest)}",
                f"💵 الربح او الخسارة : {format_num(realized_pnl, 4)}$",
                f"🧾 النسبة المئوية : {format_num(pnl_percentage, 2)}%",
                f"🕛 الوقت المستغرق : {time_spent_str}",
                f"🤔 سبب الإغلاق : {close_reason}",
                f"💸 خصم رسوم الصفقة : {format_num(closing_fee, 4)}$",
                f"💳 رصيد المحفظة : {format_num(updated_balance, 2)}$",
                "",
                "ــــــــــــــــــــــــــــــــــــ"
            ])

        # زر الرجوع للقائمة
        back_kb = InlineKeyboardMarkup().add(
            InlineKeyboardButton("🔙 رجوع للقائمة", callback_data="nav:active_profit:0")
        )

        await callback_query.message.edit_text(notification_msg, reply_markup=back_kb)
        await callback_query.answer()

    except Exception as e:
        logging.error(f"View Trade Error: {e}")
        await callback_query.answer("⚠️ حدث خطأ أثناء تحميل تفاصيل الصفقة.", show_alert=True)

# ==========================================
# 🪙 4. مستمع عرض قالب العملة المختار
# ==========================================
@dp.callback_query_handler(Text(startswith="coo_"), state="*")
async def coin_detail_handler(call: types.CallbackQuery):
    # تفكيك الكول باك (مثال: coin_ORDIUSDT_cat_vip)
    parts = call.data.split("_")
    symbol = parts[1]
    prev_category = f"{parts[2]}_{parts[3]}" 
    
    # جلب بيانات العملة المحددة من سوبابيس
    res = supabase.table("market_intelligence").select("*").eq("symbol", symbol).execute()
    coin_data = res.data
    
    if not coin_data:
        await call.answer("⚠️ حدث خطأ: لا توجد بيانات لهذه العملة.", show_alert=True)
        return
        
    coin = coin_data[0]
    
    # بناء القالب باستخدام الدالة المخصصة
    template = build_coin_template(coin)
    
    # زر الرجوع للقسم المحدد
    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("🔙 رجوع للقائمة السابقة", callback_data=prev_category))
    
    await call.message.edit_text(template, reply_markup=keyboard, parse_mode="Markdown")

import json
from aiogram import types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# دالة مساعدة لتحليل الأسباب (JSONB) بأمان
def parse_json_reasons(reasons_data):
    if not reasons_data:
        return []
    if isinstance(reasons_data, list):
        return reasons_data
    if isinstance(reasons_data, str):
        try:
            return json.loads(reasons_data)
        except:
            return [reasons_data]
    return []

# ==========================================
# 🔄 التنقل بين الصفحات (الناجحة / الفاشلة)
# ==========================================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('report_list:'))
async def process_report_list(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id, "⏳ جاري الجلب والتنسيق...")
    
    parts = callback_query.data.split(":")
    list_type = parts[1] # success or failed
    page = int(parts[2])
    
    successful, failed, _ = await fetch_and_analyze_signals() # تأكد أن هذه الدالة تدعم await إذا كانت غير متزامنة
    data_list = successful if list_type == "success" else failed
    
    # إعدادات الصفحات
    per_page = 10
    total_pages = (len(data_list) - 1) // per_page + 1 if data_list else 1
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_page_data = data_list[start_idx:end_idx]
    
    emoji = "✅" if list_type == "success" else "❌"
    text = f"{emoji} <b>سجل الصفقات | صفحة ({page + 1}/{total_pages})</b>\n\nإجمالي الإشارات: {len(data_list)}\nاختر العملة لعرض تفاصيلها:"
    
    markup = InlineKeyboardMarkup(row_width=1)
    
    # 1. أزرار العملات
    for sig in current_page_data:
        # افترضنا هنا أن fetch_and_analyze_signals ترجع قواميس تحتوي على هذه المفاتيح
        best_change = sig.get('best_change', 0.0)
        rating = sig.get('rating', 'بدون تقييم')
        btn_text = f"🪙 {sig['symbol']} ({sig['direction']}) | {best_change:.2f}% | {rating}"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"sig_view:{sig['id']}"))
        
    # 2. أزرار التنقل (السابق / التالي)
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"report_list:{list_type}:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"report_list:{list_type}:{page + 1}"))
    
    if nav_buttons:
        markup.row(*nav_buttons)
        
    markup.add(InlineKeyboardButton("🔙 عودة للوحة التحليل", callback_data="report_back"))
    
    await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id, parse_mode="HTML", reply_markup=markup)

# ==========================================
# 🕵️ عرض تفاصيل وأسرار إشارة معينة (تم تعديله ليطابق قاعدة البيانات)
# ==========================================
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('sig_view:'))
async def view_signal_details(callback_query: types.CallbackQuery):
    await bot.answer_callback_query(callback_query.id, "🔍 جاري سحب تفاصيل الرصد...")
    
    sig_id = int(callback_query.data.split(":")[1])
    
    # جلب الإشارة المحددة من سوبابيس
    res = supabase.table("radar_signals").select("*").eq("id", sig_id).execute()
    if not res.data:
        return await callback_query.answer("⚠️ عذراً، لم أجد هذه الإشارة في قاعدة البيانات!", show_alert=True)
        
    row = res.data[0]
    
    # التعامل مع القيم الفارغة بأمان
    max_price = row.get('max_price_reached') or "لم يحدد"
    min_price = row.get('min_price_reached') or "لم يحدد"
    final_change = row.get('final_change_pct') or 0.0
    status_dict = {'tracking': '🟢 قيد التتبع', 'closed': '🔴 مغلقة', 'success': '✅ ناجحة', 'failed': '❌ فاشلة'}
    current_status = status_dict.get(row.get('status', 'tracking'), row.get('status'))

    text = (
        f"📊 <b>تفاصيل الرصد: {row['symbol']} ({row['signal_type']})</b>\n"
        f"الحالة: {current_status}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 السعر وقت الإشارة: <code>{row['price']}</code>\n"
        f"📈 أعلى سعر وصل له: <code>{max_price}</code>\n"
        f"📉 أدنى سعر وصل له: <code>{min_price}</code>\n"
        f"🎯 التغير النهائي: <b>{final_change:.2f}%</b>\n\n"
    )
    
    # الأسرار (الأسباب الفنية)
    reasons = parse_json_reasons(row.get('reasons'))
    if reasons:
        text += "🕵️‍♂️ <b>الأسرار والأسباب الفنية للرصد:</b>\n"
        for r in reasons: 
            text += f" - {r}\n"
    else:
        text += "🕵️‍♂️ <i>لم يتم تسجيل أسباب فنية واضحة لهذه الإشارة.</i>\n"

    markup = InlineKeyboardMarkup()
    # زر للرجوع للقائمة السابقة
    markup.add(InlineKeyboardButton("🔙 رجوع", callback_data="report_back"))
    
    try:
        await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception as e:
        import logging
        logging.error(f"Error in sig_view: {e}")

# ==========================================
# 👑 عرض أسرار النجاح (الزر الثالث) + زر الرجوع الرئيسي
# ==========================================
@dp.callback_query_handler(lambda c: c.data in ['report_secrets', 'report_back'])
async def handle_secrets_and_back(callback_query: types.CallbackQuery):
    action = callback_query.data
    
    if action == "report_back":
        # إعادة لوحة التحكم الرئيسية
        # ملاحظة: إذا كانت analytics_dashboard_handler ترسل رسالة جديدة، قد ترغب في تمرير `edit=True` إليها إذا قمت ببرمجتها لدعم ذلك.
        await bot.answer_callback_query(callback_query.id)
        await analytics_dashboard_handler(callback_query.message)
        
    elif action == "report_secrets":
        await bot.answer_callback_query(callback_query.id, "⏳ جاري استخراج الجينات...")
        _, _, top_reasons = await fetch_and_analyze_signals() # تم إضافة await للضمان
        
        text = "👑 <b>الجينات الوراثية للصفقات الناجحة:</b>\n<i>هذه هي الأسباب الفنية التي تكررت في الصفقات الرابحة:</i>\n\n"
        if not top_reasons:
            text += "لا يوجد بيانات كافية بعد."
        else:
            for reason, count in top_reasons:
                text += f"▪️ تكرر ({count}) مرات: <b>{reason}</b>\n"
                
        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🔙 عودة للوحة التحليل", callback_data="report_back"))
        await bot.edit_message_text(text, callback_query.message.chat.id, callback_query.message.message_id, parse_mode="HTML", reply_markup=markup)

# ==========================================
# 6. معالجات الأزرار الأساسية (Secured Callbacks)
# ==========================================
@dp.callback_query_handler(lambda c: c.data == 'view_intel_report')
async def show_intelligence_report(callback_query: types.CallbackQuery):
    if callback_query.from_user.id != ADMIN_ID:
        return await callback_query.answer("❌ عذراً، هذا القسم مخصص للمالك فقط.", show_alert=True)

    report_text, markup = await get_intelligence_report_text()
    
    try:
        await callback_query.message.edit_text(
            report_text, 
            reply_markup=markup, 
            parse_mode="HTML"
        )
    except Exception as e:
        # في حال لم يتغير النص (Message is not modified)
        await callback_query.answer("تم تحديث البيانات")
       
@dp.callback_query_handler(lambda c: c.data and c.data.startswith('wallet_view:'), state="*")
async def callback_wallet_view(callback_query: types.CallbackQuery):
    user_id = int(callback_query.data.split(':')[1])
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ هذه المحفظة ليست لك!", show_alert=True)
    await process_wallet_logic(user_id, callback_query.from_user.first_name, callback=callback_query)

# --- قسم @dp.callback_query_handler(Text(startswith=' '), state="*") ---

@dp.callback_query_handler(Text(startswith='market_tab:'), state="*")
async def callback_market_tabs(callback_query: types.CallbackQuery):
    # 🔐 القفل الأمني
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    visitor_id = callback_query.from_user.id

    if visitor_id != owner_id:
        return await callback_query.answer("⚠️ هذه القائمة ليست لك!", show_alert=True)

    if not await is_authorized(callback_query): return
    
    try:
        tab_type = data_parts[2]
        # استخراج الصفحة الحالية (إذا لم توجد نبدأ من 0)
        page = int(data_parts[3]) if len(data_parts) > 3 else 0
        per_page = 24 # عدد العملات في كل صفحة
        start = page * per_page
        end = start + per_page - 1
        
        # جلب البيانات بناءً على التبويب مع تحديد النطاق (Range)
        query = supabase.table("crypto_market_simulation").select("*")
        
        if tab_type == 'gainers':
            res = query.order("change_24h", desc=True).range(start, end).execute()
            header = "📈 <b>الأعلى ربحاً (24h):</b>"
        elif tab_type == 'losers':
            res = query.order("change_24h", desc=False).range(start, end).execute()
            header = "📉 <b>الأكثر خسارة (24h):</b>"
        else: # trending
            res = query.order("volume_24h", desc=True).range(start, end).execute()
            header = "🔥 <b>الأكثر رواجاً (السيولة):</b>"
            
        if not res.data:
            return await callback_query.answer("⚠️ لا توجد عملات إضافية في هذا التبويب.", show_alert=True)

        text = f"📊 | <b>سـوق الـعـمـلات (Binance Mode)</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━\n"
        text += f"{header} (صفحة {page + 1})\n\n"
        
        markup = InlineKeyboardMarkup(row_width=2)
        
        for c in res.data:
            sym = c['symbol'].replace("USDT", "")
            price = float(c.get('current_price', 0))
            chg = float(c.get('change_24h', 0))
            
            icon = "🟢" if chg >= 0 else "🔴"
            price_format = f"{price:,.4f}" if price < 1 else f"{price:,.2f}"
            
            text += f"{icon} <b>{sym}</b> : <code>{price_format}$</code> ({chg:+.2f}%)\n"
            markup.insert(InlineKeyboardButton(f"🪙 {sym}", callback_data=f"coin_view:{owner_id}:{c['symbol']}"))

        # --- [ صف الأزرار الوظيفية (التنقل) ] ---
        nav_buttons = []
        # زر "السابق": يظهر فقط إذا لم نكن في الصفحة الأولى
        if page > 0:
            nav_buttons.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"market_tab:{owner_id}:{tab_type}:{page - 1}"))
        
        # زر "التالي": يظهر دائماً طالما أن الصفحة الحالية ممتلئة (مما يعني وجود المزيد غالباً)
        if len(res.data) == per_page:
            nav_buttons.append(InlineKeyboardButton("التالي ➡️", callback_data=f"market_tab:{owner_id}:{tab_type}:{page + 1}"))
        
        if nav_buttons:
            markup.row(*nav_buttons)

        # أزرار التبويبات الرئيسية
        markup.row(
            InlineKeyboardButton("🔥 الرائجة", callback_data=f"market_tab:{owner_id}:trending:0"),
            InlineKeyboardButton("📈 الرابحة", callback_data=f"market_tab:{owner_id}:gainers:0"),
            InlineKeyboardButton("📉 الخاسرة", callback_data=f"market_tab:{owner_id}:losers:0")
        )
        markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{owner_id}"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

    except Exception as e:
        logging.error(f"Error in market_tab: {e}")
        await callback_query.answer("⚠️ فشل تحديث بيانات السوق.", show_alert=True)
        

# --- 3. الكولباك (الذي لا يستجيب للضغط + حماية وتنظيف) --
@dp.callback_query_handler(Text(startswith='active_trades_view:'), state="*")
async def callback_view_trades(callback_query: types.CallbackQuery):
    await callback_query.answer()
    
    # تفكيك البيانات باستخدام النقطتين :
    # البيانات المتوقعة: active_trades_view:123456
    data = callback_query.data.split(':') 
    user_id = int(data[1]) # الآيدي سيكون في الخانة الثانية [1]
    
    # 🛡️ الجدار الناري
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("⚠️ ليس لديك صلاحية للوصول إلى لوحة غيرك!", show_alert=True)
    

    try:
        trades, text = await get_active_trades_report(user_id)
        
        # دالة حذف الرسالة في الخلفية
        async def delete_message_later(msg, delay=600):
            await asyncio.sleep(delay)
            try:
                await msg.delete()
            except:
                pass # تجاهل الخطأ لو المستخدم حذفها يدوياً
                
        if not trades:
            msg = await callback_query.message.edit_text(
                text, 
                reply_markup=get_market_keyboard(user_id), 
                parse_mode="HTML"
            )
        else:
            msg = await callback_query.message.edit_text(
                text, 
                reply_markup=get_trades_keyboard(user_id, trades), 
                parse_mode="HTML"
            )
            
        # تشغيل المؤقت (5 دقائق = 300 ثانية)
        asyncio.create_task(delete_message_later(callback_query.message, 600))
        
    except Exception as e:
        logging.error(f"Callback View Error: {e}")
        await callback_query.message.answer(f"❌ فشل عرض الصفقات.")             

# --- [ 2. هاندلر عرض الشارت التفاعلي ] ---
@dp.callback_query_handler(Text(startswith='coin_view:'), state="*")
async def process_coin_view(callback_query: types.CallbackQuery):
    try:
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        symbol = data_parts[2]
        tf = data_parts[3] if len(data_parts) > 3 else "15m"
        visitor_id = callback_query.from_user.id

        if visitor_id != owner_id:
            return await callback_query.answer("⚠️ هذه البيانات ليست لك!", show_alert=True)

        res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not res.data:
            return await callback_query.answer("⚠️ العملة غير موجودة!", show_alert=True)
            
        coin = res.data[0]
        price = float(coin.get('current_price', 0))
        high = float(coin.get('high_24h', 0))
        low = float(coin.get('low_24h', 0))
        change = float(coin.get('change_24h', 0))
        
        # --- [ استدعاء البيانات الاستخباراتية المحدثة ] ---
        # بيانات السيولة (OBV)
        vol_current = float(coin.get(f'volume_{tf}', 0))
        obv_now = float(coin.get(f'obv_{tf}', 0))
        obv_prev = float(coin.get(f'obv_prev_{tf}', 0))
        obv_slope = float(coin.get(f'obv_slope_{tf}', 0))
        
        # بيانات عرض القناة (BBW) - "فم التمساح"
        bbw_now = float(coin.get(f'bbw_{tf}', 0))
        bbw_prev = float(coin.get(f'bbw_prev_{tf}', 0))
        
        # حساب نسبة الانفجار (Expansion Ratio)
        expansion = (bbw_now / bbw_prev * 100) if bbw_prev > 0 else 100

        # مؤشرات الشارت
        ema20 = float(coin.get(f'ema_20_{tf}', price))
        ema50 = float(coin.get(f'ema_50_{tf}', price))
        ema100 = float(coin.get(f'ema_100_{tf}', price))
        bb_up = float(coin.get(f'bb_upper_{tf}', price))
        bb_mid = float(coin.get(f'bb_middle_{tf}', price))
        bb_low = float(coin.get(f'bb_lower_{tf}', price))
        rsi = float(coin.get(f'rsi_{tf}', 50))

        def f_num(val): return f"{val:,.4f}" if val < 1 else f"{val:,.2f}"
        
        # أيقونات ذكية للحالة
        expansion_icon = "🔥" if expansion > 110 else "💤"
        obv_icon = "🌊" if obv_slope > 0 else "📉"

        # ترتيب الشارت الديناميكي
        chart_elements = [
            {"name": "البولنجر العلوي", "val": bb_up, "icon": "🟡"},
            {"name": "البولنجر الأوسط", "val": bb_mid, "icon": "⚪"},
            {"name": "البولنجر السفلي", "val": bb_low, "icon": "🟡"},
            {"name": "خط EMA 100", "val": ema100, "icon": "🔵"},
            {"name": "خط EMA 50", "val": ema50, "icon": "🟢"},
            {"name": "خط EMA 20", "val": ema20, "icon": "🔴"},
            {"name": "سعر العملة الحالي", "val": price, "icon": "💵"}
        ]
        chart_elements.sort(key=lambda x: x["val"], reverse=True)

        # 📝 [ بناء الرسالة النهائية الاستخباراتية ]
        text = f"<b>{symbol.replace('USDT', '')} / USDT</b> | ⏱ {tf}\n"
        text += f"💰 السعر: <code>{f_num(price)}</code> ({change:+.2f}%)\n"
        text += f"🔝 أعلى: <code>{f_num(high)}</code> | 🔙 أدنى: <code>{f_num(low)}</code>\n"
        
        text += "----------------------\n"
        text += f"💎 <b>قسم استخبارات السيولة (OBV):</b>\n"
        text += f"• الحالي: <code>{obv_now:,.0f}</code>\n"
        text += f"• السابق: <code>{obv_prev:,.0f}</code>\n"
        text += f"{obv_icon} الميل (Slope): <code>{obv_slope:,.0f}</code>\n"
        
        text += "----------------------\n"
        text += f"🐊 <b>قوة الانفجار (BBW):</b>\n"
        text += f"• عرض القناة: <code>{bbw_now:.4f}</code>\n"
        text += f"{expansion_icon} نسبة التوسع: <code>{expansion:.1f}%</code>\n"
        
        text += "----------------------\n"
        for el in chart_elements:
            text += f"{el['icon']}: {el['name']} {{ <code>{f_num(el['val'])}</code> }}\n"
            
        text += "----------------------\n"
        text += f"📈 RSI 14: <b>{rsi:.1f}</b> | 🧭 OBV/V: <code>{vol_current:,.0f}</code>\n"
        text += "⚠️ <i>إعداداتك الذهبية: RSI (22 / 78)</i>\n"
        text += "===================="

        await callback_query.message.edit_text(
            text, 
            reply_markup=get_coin_keyboard(owner_id, symbol, tf), 
            parse_mode="HTML"
        )
        await callback_query.answer()
    except Exception as e:
        print(f"Error: {e}")
        await callback_query.answer("❌ حدث خطأ في معالجة البيانات.")
        


# 🛠️ [ أداة تحليل المخاطر المحسنة - جدار الحماية ]
def evaluate_reversal_risk(current_price, support_1d, resistance_1d, direction):
    try:
        if direction == "LONG":
            distance_to_res = (resistance_1d - current_price) / current_price
            risk_score = 99 if distance_to_res < 0.01 else max(10, 100 - (distance_to_res * 1000))
            return min(risk_score, 99)
        elif direction == "SHORT":
            distance_to_sup = (current_price - support_1d) / current_price
            risk_score = 99 if distance_to_sup < 0.01 else max(10, 100 - (distance_to_sup * 1000))
            return min(risk_score, 99)
    except ZeroDivisionError:
        return 50

# 🚀 [ غرفة العمليات الـ VIP - خوارزمية كشف النوايا والانفجار ]
@dp.callback_query_handler(Text(startswith='vip_signal:'), state="*")
async def process_vip_signal(callback_query: types.CallbackQuery):
    def f_num(val): 
        if val is None or val == 0: return "0.00"
        return f"{val:.5f}".rstrip('0').rstrip('.') if val < 1 else f"{val:.4f}"

    try:
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        symbol = data_parts[2]

        if callback_query.from_user.id != owner_id:
            return await callback_query.answer("⚠️ مستوى أمني غير كافٍ!", show_alert=True)

        res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not res.data: 
            return await callback_query.answer("❌ لا توجد بيانات كافية.", show_alert=True)
        
        c = res.data[0]
        price = float(c['current_price'])
        
        # --- 1️⃣ سحب البيانات الأساسية ---
        obv_slope_15m = float(c.get('obv_slope_15m', 0))
        orderbook_imb = float(c.get('orderbook_imbalance_ratio', 1.0))
        whale_absorption = c.get('whale_absorption_detected', False)
        
        bb_up_15m = float(c.get('bb_upper_15m', price * 1.01))
        bb_low_15m = float(c.get('bb_lower_15m', price * 0.99))
        kc_up_15m = float(c.get('kc_upper_15m', price * 1.02))
        kc_low_15m = float(c.get('kc_lower_15m', price * 0.98))
        bbw_15m = float(c.get('bbw_15m', 0.05))
        bbw_prev_15m = float(c.get('bbw_prev_15m', 0.05))
        
        is_squeezed = (bb_up_15m < kc_up_15m) and (bb_low_15m > kc_low_15m)
        is_expanding = bbw_15m > bbw_prev_15m
        
        ema20_15m = float(c.get('ema_20_15m', price))
        ema50_15m = float(c.get('ema_50_15m', price))
        rsi_15m = float(c.get('rsi_15m', 50))
        macd_15m = float(c.get('macd_15m', 0))
        macd_sig_15m = float(c.get('macd_signal_15m', 0))
        atr_15m = float(c.get('atr_15m', price * 0.01))

        support_1h = float(c.get('support_1h', price * 0.98))
        res_1h = float(c.get('resistance_1h', price * 1.02))
        support_1d = float(c.get('support_1d', price * 0.85))
        res_1d = float(c.get('resistance_1d', price * 1.15))

        # --- 📐 سحب بيانات البرايس أكشن والترند ---
        trend_1h = c.get('1h_trend_direction', 'SIDEWAY')
        channel_1h_status = c.get('1h_channel_status', 'NONE')
        pattern_15m = c.get('15m_pattern_name', 'NONE')
        pattern_class = c.get('15m_pattern_class', 'NONE')

        # --- 🧠 2️⃣ محرك القرار المتقدم (نظام النقاط الشامل) ---
        bull_score = 0
        bear_score = 0
        
        # أ. تقييم السيولة والحيتان (Weight: 30)
        if orderbook_imb > 1.05: bull_score += 15
        elif orderbook_imb < 0.95: bear_score += 15
        
        if obv_slope_15m > 0: bull_score += 15
        elif obv_slope_15m < 0: bear_score += 15
        
        # ب. تقييم المؤشرات الفنية (Weight: 30)
        if price > ema50_15m: bull_score += 10
        else: bear_score += 10
            
        if macd_15m > macd_sig_15m: bull_score += 10
        else: bear_score += 10
            
        if rsi_15m > 55 and rsi_15m < 78: bull_score += 10
        elif rsi_15m < 45 and rsi_15m > 22: bear_score += 10

        # ج. تقييم الحيتان (Weight: 10)
        if whale_absorption and orderbook_imb > 1: bull_score += 10
        elif whale_absorption and orderbook_imb < 1: bear_score += 10

        # د. تقييم البرايس أكشن والترند والنماذج (Weight: 30)
        if trend_1h == "UP": bull_score += 10
        elif trend_1h == "DOWN": bear_score += 10

        if channel_1h_status in ["BREAKOUT_UP", "RETEST_UP"]: bull_score += 10
        elif channel_1h_status in ["BREAKOUT_DOWN", "RETEST_DOWN"]: bear_score += 10

        bullish_patterns = ["Bullish Flag", "Bullish Pennant", "Symmetrical Triangle", "Ascending Triangle", "Falling Wedge", "Double Bottom", "Inverted Head and Shoulders"]
        bearish_patterns = ["Bearish Flag", "Bearish Pennant", "Descending Triangle", "Rising Wedge", "Double Top", "Head and Shoulders"]

        if pattern_15m in bullish_patterns and rsi_15m < 78: bull_score += 10
        elif pattern_15m in bearish_patterns and rsi_15m > 22: bear_score += 10

        # --- 📊 3️⃣ تحديد الاتجاه النهائي بناءً على المنتصر ---
        total_score = bull_score + bear_score
        if total_score == 0: total_score = 1
        
        if bull_score >= bear_score:
            trade_direction = "LONG"
            direction_text = "شراء (LONG) 🟢"
            emoji_trend = "🚀"
            confidence_rate = min((bull_score / 100) * 100 * 1.2, 99) # Boost confidence slightly if elements align
        else:
            trade_direction = "SHORT"
            direction_text = "بيع (SHORT) 🔴"
            emoji_trend = "📉"
            confidence_rate = min((bear_score / 100) * 100 * 1.2, 99)

        risk_percentage = evaluate_reversal_risk(price, support_1d, res_1d, trade_direction)
        
        # --- ⏳ 4️⃣ تحديد التوقيت الزمني للحركة ---
        if channel_1h_status in ["BREAKOUT_UP", "BREAKOUT_DOWN"]:
            time_estimate = "الآن (انفجار سيولة 🌊)"
            move_when = "تم كسر القناة السعرية بقوة"
        elif channel_1h_status in ["RETEST_UP", "RETEST_DOWN"]:
            time_estimate = "جاهز للانطلاق 🎯"
            move_when = "نهاية إعادة الاختبار (قنص الارتداد)"
        elif is_expanding:
            time_estimate = "الآن (بدأ تدفق السيولة 🌊)"
            move_when = "السعر يتحرك في هذه اللحظة"
        elif is_squeezed:
            time_estimate = "خلال 15 - 45 دقيقة ⏳"
            move_when = "بعد كسر الانضغاط السعري (Squeeze Breakout)"
        else:
            time_estimate = "خلال 1 - 4 ساعات 🕰️"
            move_when = "حركة اعتيادية متدرجة"

        # --- 🎯 5️⃣ تحديد الأهداف ونقاط الدخول (دخول هجومي متقدم) ---
        if trade_direction == "LONG":
            entry_1 = price
            # دخول هجومي على دعم قوي مثل EMA20 أو بعد إعادة اختبار القناة
            entry_2 = ema20_15m if ema20_15m < price else price * 0.995
            dca = ema50_15m
            sl = ema50_15m - (atr_15m * 1.5)
            
            tp1 = res_1h if (res_1h - price) > (atr_15m * 1.2) else price + (atr_15m * 1.5)
            tp2 = tp1 + (atr_15m * 2.0)
            tp3 = min(res_1d, tp2 + (atr_15m * 3.5))
        else:
            entry_1 = price
            entry_2 = ema20_15m if ema20_15m > price else price * 1.005
            dca = ema50_15m
            sl = ema50_15m + (atr_15m * 1.5)
            
            tp1 = support_1h if (price - support_1h) > (atr_15m * 1.2) else price - (atr_15m * 1.5)
            tp2 = tp1 - (atr_15m * 2.0)
            tp3 = max(support_1d, tp2 - (atr_15m * 3.5))

        stars = "⭐" * int(confidence_rate / 20) if confidence_rate >= 20 else "⭐"

        # تجهيز نصوص البرايس أكشن للعرض
        trend_display = "صاعد 📈" if trend_1h == "UP" else "هابط 📉" if trend_1h == "DOWN" else "عرضي ↔️"
        pattern_display = f"نموذج {pattern_15m} ({'إيجابي' if pattern_15m in bullish_patterns else 'سلبي'})" if pattern_15m != "NONE" else "لا يوجد"
        
        channel_display = "مستقرة داخل النطاق"
        if "BREAKOUT" in channel_1h_status: channel_display = "🔥 اختراق قوي للقناة السعرية"
        elif "RETEST" in channel_1h_status: channel_display = "🎯 إعادة اختبار ناجحة (فرصة قنص)"

        # --- 📝 6️⃣ القالب النهائي (VIP) ---
        signal_text = f"🔥 <b> القنص المتقدم :</b> #{symbol} {emoji_trend}\n"
        signal_text += f"ــــــــــــــــــــــــــــــــــــــــــــــــــ\n\n"
        
        signal_text += f"📊 <b>الوضع الفني والبرايس أكشن :</b>\n"
        signal_text += f"• القرار: <b>{direction_text}</b>\n"
        signal_text += f"• جودة الصفقة: {stars} ({confidence_rate:.0f}%)\n"
        signal_text += f"• الترند العام (1H): <b>{trend_display}</b>\n"
        signal_text += f"• حالة القناة: <b>{channel_display}</b>\n"
        if pattern_15m != "NONE":
            signal_text += f"• النماذج الفنية: <b>{pattern_display}</b>\n"
        signal_text += f"• نسبة المخاطرة: <b>{risk_percentage:.0f}%</b> {'🟢' if risk_percentage < 40 else '🟡' if risk_percentage < 70 else '🔴'}\n\n"

        signal_text += f"⏳ <b>التوقيت الزمني للحركة:</b>\n"
        signal_text += f"• متى سيتحرك؟: <b>{move_when}</b>\n"
        signal_text += f"• المدة المتوقعة: <b>{time_estimate}</b>\n\n"
        
        signal_text += f"📐 <b>خطة الهجوم الموصى بها:</b>\n"
        signal_text += f"🎯 مناطق الدخول: <code>{f_num(entry_2)}</code> - <code>{f_num(entry_1)}</code>\n"
        signal_text += f"🛡️ نقطة التبريد (DCA): <code>{f_num(dca)}</code>\n"
        signal_text += f"🚫 وقف الخسارة (SL): <code>{f_num(sl)}</code>\n\n"
        
        signal_text += f"💰 <b>محطات جني الأرباح:</b>\n"
        signal_text += f"1️⃣ الهدف الأول: <code>{f_num(tp1)}</code> ⚡\n"
        signal_text += f"2️⃣ الهدف الثاني: <code>{f_num(tp2)}</code> 🚀\n"
        signal_text += f"3️⃣ الهدف الثالث: <code>{f_num(tp3)}</code> 🐋\n"

        back_kb = InlineKeyboardMarkup().add(InlineKeyboardButton("🔙 رجوع", callback_data=f"coin_view:{owner_id}:{symbol}:15m"))
        await callback_query.message.edit_text(signal_text, reply_markup=back_kb, parse_mode="HTML")

    except Exception as e:
        print(f"VIP Error: {e}")
        await callback_query.answer("❌ تعذر التوليد. حدث خطأ أثناء تحليل البيانات.", show_alert=True)
        
# ==========================================
# 7. معالجات دورة الصفقة (المطورة لدعم الفواصل والأمان)
# ==========================================
@dp.callback_query_handler(Text(startswith='setup_trade:'), state="*")
async def process_setup_trade(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ المتصفح ليس لك!", show_alert=True)

    symbol = data_parts[2]
    side = data_parts[3]
    
    try:
        # جلب السعر والمستويات (High/Low)
        coin_res = supabase.table("crypto_market_simulation").select("*").eq("symbol", symbol).execute()
        if not coin_res.data:
            return await callback_query.answer("⚠️ العملة غير متوفرة.", show_alert=True)
            
        coin = coin_res.data[0]
        price = float(coin['current_price'])
        balance = await get_user_bank_balance(owner_id)
        
        # تخزين الجلسة مع إضافة بيانات الـ High و Low ومفتاح للمناطق
        trade_sessions[owner_id] = {
            'symbol': symbol,
            'side': side,
            'market_price': price,        # السعر المباشر
            'entry_price': price,         # السعر المعتمد (قد يتغير لو اختار منطقة)
            'selected_entry_price': None, # لحفظ السعر المختار يدوياً
            'high_24h': float(coin.get('high_24h', price)),
            'low_24h': float(coin.get('low_24h', price)),
            'leverage': 10,
            'margin_pct': 25,
            'balance': float(balance),
            'show_zones': False           # لإظهار/إخفاء أزرار المناطق
        }
        
        # حذفنا المدة كما طلبت، وسنبدأ التحديث اللحظي
        await update_trade_ui(callback_query)
        
    except Exception as e:
        print(f"Error: {e}")
        await callback_query.answer("⚠️ خطأ في التجهيز.")
        

@dp.callback_query_handler(Text(startswith='trade_cycle:'), state="*")
async def process_trade_cycle(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ المتصفح ليس لك!", show_alert=True)

    if owner_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة.")
    
    action = data_parts[2]
    session = trade_sessions[owner_id]
    
    # تحديث القيم في الجلسة
    if action == 'leverage':
        idx = LEVERAGE_LEVELS.index(session['leverage'])
        session['leverage'] = LEVERAGE_LEVELS[(idx + 1) % len(LEVERAGE_LEVELS)]
    elif action == 'margin':
        idx = MARGIN_PCT_LEVELS.index(session['margin_pct'])
        session['margin_pct'] = MARGIN_PCT_LEVELS[(idx + 1) % len(MARGIN_PCT_LEVELS)]
    
    # الإجابة على الكولباك لمنع ظهور الساعة الرملية
    await callback_query.answer(f"تم تحديث {action}")
    
    # استدعاء التحديث (الدالة ستحمي نفسها من التكرار)
    await update_trade_ui(callback_query)

@dp.callback_query_handler(Text(startswith='trade_zones:'), state="*")
async def handle_trade_zones_activation(callback_query: types.CallbackQuery):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    
    if user_id not in trade_sessions:
        return await callback_query.answer("⚠️ الجلسة منتهية.")

    # تفعيل عرض المناطق
    trade_sessions[user_id]['show_zones'] = True
    
    await callback_query.answer("🎯 جاري استخراج مناطق الدخول...")
    
    # التحديث فوراً
    await update_trade_ui(callback_query)

@dp.callback_query_handler(Text(startswith='set_zone:'), state="*")
async def handle_set_zone(callback_query: types.CallbackQuery):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    value = data[2]

    if user_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة.")

    if value == "market":
        trade_sessions[user_id]['selected_entry_price'] = None
    else:
        # تحديد السعر المختار يدوياً (Limit Order)
        trade_sessions[user_id]['selected_entry_price'] = float(value)
        trade_sessions[user_id]['entry_price'] = float(value)

    await callback_query.answer("📍 تم تحديد سعر الدخول")
    await update_trade_ui(callback_query)
        

@dp.callback_query_handler(Text(startswith='trade_confirm:'), state="*")
async def process_trade_confirm(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ لا يمكنك تأكيد صفقة غيرك!", show_alert=True)

    if owner_id not in trade_sessions:
        return await callback_query.answer("⚠️ انتهت الجلسة.", show_alert=True)
        
    session = trade_sessions[owner_id]
    
    # حساب الهامش
    margin_amount = session['balance'] * (session['margin_pct'] / 100.0)
    
    # فحص هل هي صفقة "معلقة" (Limit) أم "فورية" (Market)
    is_limit = session.get('selected_entry_price') is not None
    is_active_status = not is_limit  # إذا كان ليميت تكون False
    
    # السعر المعتمد للتنفيذ
    exec_price = session['entry_price']

    try:
        # الحسابات الدقيقة
        quantity = (margin_amount * session['leverage']) / exec_price
        liq_price = calculate_liquidation(exec_price, session['leverage'], session['side'])
        
        new_balance = session['balance'] - margin_amount
        
        # 1. تحديث الرصيد (يتم خصم المبلغ بمجرد فتح الطلب سواء معلق أو فوري لضمان الجدية)
        supabase.table("users_global_profile").update({
            "bank_balance": float(new_balance) 
        }).eq("user_id", owner_id).execute()
        
        # 2. إدخال البيانات في active_trades
        trade_data = {
            "user_id": owner_id,
            "symbol": session['symbol'],
            "side": session['side'],
            "entry_price": exec_price,
            "leverage": session['leverage'],
            "margin": margin_amount,
            "quantity": quantity,
            "liquidation_price": liq_price,
            "is_active": is_active_status, # التعديل الجوهري هنا ✅
            "created_at": datetime.now().isoformat()
        }
        
        supabase.table("active_trades").insert(trade_data).execute()
        
        # 3. عرض رسالة النجاح
        status_text = "⚡ صفقة فورية نشطة" if is_active_status else "⏳ طلب معلق (Limit)"
        
        text = f"✅ <b>تم تنفيذ العملية بنجاح!</b>\n\n"
        text += f"الحالة: {status_text}\n"
        text += f"العملة: #{session['symbol']}\n"
        text += f"سعر الدخول: <code>{exec_price:,.4f} $</code>\n"
        text += f"المبلغ المحجوز: <code>{margin_amount:,.2f} $</code>\n"
        text += f"الرصيد المتبقي: <code>{new_balance:,.2f} $</code>"
        
        # تنظيف الجلسة
        del trade_sessions[owner_id]
        
        markup = InlineKeyboardMarkup()
        btn_text = "📋 صفقاتي النشطة" if is_active_status else "⏳ طلباتي المعلقة"
        markup.add(InlineKeyboardButton(btn_text, callback_data=f"active_trades_view:{owner_id}"))
        markup.add(InlineKeyboardButton("🔙 العودة للسوق", callback_data=f"market_tab:{owner_id}:trending"))
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
          
    except Exception as e:
        print(f"Trade Confirmation Error: {e}")
        await callback_query.answer("❌ فشل تنفيذ الصفقة.")

        
@dp.callback_query_handler(Text(startswith='cancel_limit:'), state="*")
async def cancel_limit_order(callback_query: types.CallbackQuery):
    data_parts = callback_query.data.split(':')
    owner_id = int(data_parts[1])
    trade_id = data_parts[2]
    
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ لا يمكنك إلغاء طلب غيرك!", show_alert=True)

    try:
        # 1. جلب بيانات الصفقة للتأكد من مبلغ الهامش (Margin)
        trade_res = supabase.table("active_trades").select("*").eq("trade_id", trade_id).execute()
        if not trade_res.data:
            return await callback_query.answer("⚠️ الطلب غير موجود أو تم تنفيذه بالفعل.", show_alert=True)
            
        trade = trade_res.data[0]
        refund_amount = float(trade['margin'])
        
        # 2. جلب رصيد المستخدم الحالي لإعادة المال
        balance = await get_user_bank_balance(owner_id)
        new_balance = float(balance) + refund_amount
        
        # 3. تنفيذ العمليات (تحديث الرصيد وحذف الصفقة)
        # تحديث الرصيد
        supabase.table("users_global_profile").update({"bank_balance": new_balance}).eq("user_id", owner_id).execute()
        
        # حذف الطلب المعلق
        supabase.table("active_trades").delete().eq("trade_id", trade_id).execute()
        
        await callback_query.answer(f"✅ تم إلغاء الطلب وإعادة {refund_amount:,.2f}$ لمحفظتك.", show_alert=True)
        
        # تحديث القائمة بعد الحذف
        await pending_trades_view(callback_query)
        
    except Exception as e:
        print(f"Cancel Error: {e}")
        await callback_query.answer("❌ فشل إلغاء الطلب.")
        
# ==========================================
# --- [ المعالجات Handlers المحدثة ] ---
# ==========================================

# 1. معالج اختيار الهدف والتأكيد (دعم الفواصل العشرية)
@dp.callback_query_handler(Text(startswith=('pr_sl_', 'pr_tp_')), state="*")
async def handle_automated_risk_selection(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') # الهيكلية: pr_sl_uid_tid_price
        risk_type = data[1]
        btn_user_id = int(data[2])
        trade_id = data[3]
        # 🟢 تعديل: تحويل السعر لـ float بدلاً من int لدعم العملات الرخيصة
        target_price = float(data[4]) 

        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ هذه الصلاحية ليست لك! 🚫", show_alert=True)

        res = supabase.table("active_trades").select("*").eq("trade_id", trade_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة مغلقة.")
        
        trade = res.data[0]
        # 🟢 تعديل: جلب القيم كـ float لضمان دقة الحسابات
        entry = float(trade['entry_price'])
        liq = float(trade['liquidation_price'])
        side = trade['side']
        lev = int(trade['leverage'])
        margin = float(trade['margin'])

        # فحص التصفية (Liquidation Check)
        if risk_type == "sl":
            if (side == "LONG" and target_price <= liq) or (side == "SHORT" and target_price >= liq):
                p_fmt = f"{target_price:,.4f}" if target_price < 1 else f"{target_price:,.2f}"
                return await callback_query.answer(f"⚠️ السعر {p_fmt} خلف التصفية!", show_alert=True)

        # حسابات الربح والخسارة المتوقعة بدقة
        diff = (target_price - entry) if side == "LONG" else (entry - target_price)
        pnl_pct = (diff / entry) * lev * 100
        expected_cash = margin * (pnl_pct / 100)

        label = "إيقاف الخسارة (SL)" if risk_type == "sl" else "جني الأرباح (TP)"
        status_icon = "✅ حماية" if pnl_pct > 0 else "📉 مخاطرة"
        
        # تنسيق السعر للعرض
        p_fmt = f"{target_price:,.4f}" if target_price < 1 else f"{target_price:,.2f}"

        text = f"⚖️ <b>تأكيد مستهدف {label}</b>\n"
        text += f"━━━━━━━━━━━━━━\n"
        text += f"• السعر المختار: <code>{p_fmt} $</code>\n"
        text += f"• الحالة: <b>{status_icon}</b>\n"
        text += f"• النسبة المتوقعة: <b>{pnl_pct:+.2f}%</b>\n"
        text += f"• الربح/الخسارة: <b>{expected_cash:+.2f} $</b>\n\n"
        text += "هل تريد اعتماد هذا المستهدف وحفظه؟"

        # حفظ الكولباك (ملاحظة: تليجرام لده حد 64 بايت، لذا نرسل السعر كما هو)
        save_callback = f"c_{risk_type}_{btn_user_id}_{trade_id}_{data[4]}"
        
        markup = InlineKeyboardMarkup(row_width=1).add(
            InlineKeyboardButton("✅ نعم، تأكيد الحفظ", callback_data=save_callback),
            InlineKeyboardButton("❌ تراجع (العودة)", callback_data=f"exp_risk_{btn_user_id}_{trade_id}")
        )

        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        import logging
        logging.error(f"Error in automated risk: {e}")
        await callback_query.answer("⚠️ خطأ في المعالجة.")

# 2. معالج الحفظ النهائي (دعم numeric)
@dp.callback_query_handler(Text(startswith=('c_sl_', 'c_tp_')), state="*")
async def commit_risk_to_db(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_')
        risk_type = data[1]
        btn_user_id = int(data[2])
        t_id = data[3]
        # 🟢 تعديل: حفظ السعر كـ float
        new_price = float(data[4]) 

        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ عذراً، لا تملك الصلاحية! 🚫", show_alert=True)

        column_name = "stop_loss" if risk_type == "sl" else "take_profit"
        label = "وقف الخسارة" if risk_type == "sl" else "جني الأرباح"

        # التحديث في سوبابيس (numeric يقبل float)
        supabase.table("active_trades").update({
            column_name: new_price
        }).eq("trade_id", t_id).execute()
        
        await callback_query.answer(f"✅ تم حفظ {label} بنجاح!", show_alert=True)
        
        # إعادة التوجيه للوحة الإدارة
        callback_query.data = f"manage_trade:{t_id}"
        await callback_manage_trade_handler(callback_query)
        
    except Exception as e:
        import logging
        logging.error(f"Error in commit_risk: {e}")
        await callback_query.answer("❌ خطأ في الحفظ.")

# 3. معالج التوسع (دعم الفواصل في الأسعار الحالية)
@dp.callback_query_handler(Text(startswith='exp_'), state="*")
async def handle_expansion_protected(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') 
        section = data[1]
        btn_user_id = int(data[2])
        t_id = data[3]        
        
        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ مبعسس! هذه الأزرار ليست لك. 🚫", show_alert=True)

        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة.")
        
        trade = res.data[0]
        
        # 🟢 جلب سعر السوق الحالي بالفواصل
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else float(trade['entry_price'])

        # استدعاء دالة العرض (تأكد أن get_trade_settings_view تدعم float)
        text, markup = get_trade_settings_view(trade, current_price, expand_section=section)
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()

    except Exception as e:
        import logging
        logging.error(f"Expansion Error: {e}")
        await callback_query.answer("❌ حدث خطأ داخلي.")
       

# 4. معالج فتح لوحة الإعدادات (Main Gate)
@dp.callback_query_handler(Text(startswith='manage_trade:'), state="*")
async def callback_manage_trade_handler(callback_query: types.CallbackQuery):
    try:
        t_id = callback_query.data.split(':')[1]
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        
        if not res.data:
            return await callback_query.answer("⚠️ الصفقة غير موجودة أو أغلقت.", show_alert=True)
        
        trade = res.data[0]
        # 🛡️ التأكد من صاحب الصفقة
        if callback_query.from_user.id != int(trade['user_id']):
            return await callback_query.answer("⚠️ لا يمكنك إدارة صفقات الآخرين!", show_alert=True)

        # جلب السعر الحالي بالفواصل العشرية
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = float(coin_res.data[0]['current_price']) if coin_res.data else float(trade['entry_price'])

        # إرسال البيانات لدالة العرض (تأكد أن الدالة get_trade_settings_view تقبل float)
        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Error in manage_trade: {e}")
        await callback_query.answer("❌ خطأ في فتح الإعدادات.")

 # ==========================================
# --- [ بوابة تأكيد التنفيذ ] ---
# ==========================================
@dp.callback_query_handler(Text(startswith='conf_'), state="*")
async def security_gate_protected(callback_query: types.CallbackQuery):
    try:
        # تفكيك البيانات: conf_action_percent_uid_tid
        _, action, percent, u_id, t_id = callback_query.data.split('_')
        
        if callback_query.from_user.id != int(u_id):
            return await callback_query.answer("⚠️ لا تتدخل في صفقات غيرك! 🚫", show_alert=True)

        res = supabase.table("active_trades").select("symbol").eq("trade_id", t_id).execute()
        if not res.data: 
            return await callback_query.message.edit_text("⚠️ الصفقة مغلقة أو غير موجودة.")
        
        symbol = res.data[0]['symbol']
        act_name = "إغلاق جزء من المركز" if percent != "100" else "إغلاق المركز بالكامل"
        
        text = f"🛡️ <b>تأكيـد التنفيذ: #{symbol}</b>\n"
        text += f"━━━━━━━━━━━━━━━━━━\n"
        text += f"• الإجراء: <b>{act_name}</b>\n"
        text += f"• النسبة: <b>{percent}%</b>\n\n"
        text += "⚠️ <b>سيتم التنفيذ فوراً بسعر السوق الحالي، هل أنت متأكد؟</b>"
        
        markup = InlineKeyboardMarkup(row_width=2).add(
            InlineKeyboardButton("✅ نعم، تنفيذ", callback_data=f"exe_{action}_{percent}_{u_id}_{t_id}"),
            InlineKeyboardButton("❌ تراجع", callback_data=f"manage_trade:{t_id}")
        )
        
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer()
    except Exception as e:
        logging.error(f"Security Gate Error: {e}")
        await callback_query.answer("❌ خطأ في بوابة التأكيد.")
        

# ==========================================
# --- [ محرك التنفيذ الموحد: الإغلاق فقط ] ---
# ==========================================
@dp.callback_query_handler(Text(startswith='exe_'), state="*")
async def universal_execution_engine(callback_query: types.CallbackQuery):
    try:
        _, action, percent_str, u_id, t_id = callback_query.data.split('_')
        percent = int(percent_str)
        user_id = int(u_id)

        if callback_query.from_user.id != user_id:
            return await callback_query.answer("⚠️ لا تتدخل في صفقات غيرك!", show_alert=True)

        # جلب بيانات المستخدم والصفقة
        account = await get_trading_account_snapshot(user_id)
        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data: return await callback_query.message.edit_text("❌ الصفقة غير موجودة.")
        
        trade = res.data[0]
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        # 🟢 استخدام float للسعر الحالي
        cur_price = float(coin_res.data[0]['current_price'])
        
        success_text = ""

        if action == 'cl':
            # حساب الكميات المغلقة بدقة float
            m_to_close = float(trade['margin']) * (percent / 100.0)
            q_to_close = float(trade['quantity']) * (percent / 100.0)
            
            # 🟢 حساب PNL الدقيق
            entry_price = float(trade['entry_price'])
            if trade['side'] == 'LONG':
                pnl_pct = (cur_price - entry_price) / entry_price
            else:
                pnl_pct = (entry_price - cur_price) / entry_price
                
            pnl_amt = m_to_close * pnl_pct * float(trade['leverage'])
            ret_to_bank = m_to_close + pnl_amt

            # تحديث البنك (بدون int لضمان حفظ السنتات)
            new_bank = max(0.0, float(account['free_cash']) + ret_to_bank)
            supabase.table("users_global_profile").update({"bank_balance": new_bank}).eq("user_id", user_id).execute()

            if percent >= 100:
                supabase.table("active_trades").delete().eq("trade_id", t_id).execute()
                success_text = f"✅ <b>تم إغلاق المركز بالكامل: #{trade['symbol']}</b>\n"
            else:
                # تحديث الصفقة (طرح الهامش والكمية المغلقة)
                supabase.table("active_trades").update({
                    "margin": float(trade['margin']) - m_to_close,
                    "quantity": float(trade['quantity']) - q_to_close
                }).eq("trade_id", t_id).execute()
                success_text = f"✂️ <b>تم إغلاق جزئي {percent}%: #{trade['symbol']}</b>\n"

            pnl_emoji = "🟢" if pnl_amt >= 0 else "🔴"
            # تنسيق عرض الأسعار
            e_fmt = f"{entry_price:,.4f}" if entry_price < 1 else f"{entry_price:,.2f}"
            c_fmt = f"{cur_price:,.4f}" if cur_price < 1 else f"{cur_price:,.2f}"
            
            success_text += f"• سعر الدخول: <b>{e_fmt} $</b>\n• سعر الإغلاق: <b>{c_fmt} $</b>\n"
            success_text += f"• الربح/الخسارة: <b>{pnl_amt:+.2f} $</b> {pnl_emoji}\n"
            success_text += f"• العائد للبنك: <b>{ret_to_bank:,.2f} $</b>"

            msg = await callback_query.message.edit_text(success_text, parse_mode="HTML")
            await asyncio.sleep(10)
            try: await msg.delete()
            except: pass

            # تحديث العرض للمستخدم
            trades_left = supabase.table("active_trades").select("trade_id").eq("user_id", user_id).execute()
            if not trades_left.data:
                from bot_handlers import send_main_portfolio
                await send_main_portfolio(callback_query.message, user_id)
            else:
                callback_query.data = f"active_trades_view:{user_id}"
                from bot_handlers import callback_view_trades
                await callback_view_trades(callback_query)

    except Exception as e:
        logging.error(f"Logic Error: {e}")
        await callback_query.answer("❌ حدث خطأ في الحسابات.")
# ==========================================
# 9. زر العودة للوحة التحكم الرئيسية للصفقة (Back Button)
# ==========================================
@dp.callback_query_handler(Text(startswith='back_ts_'), state="*")
async def back_to_settings_protected(callback_query: types.CallbackQuery):
    try:
        data = callback_query.data.split('_') # الهيكلية: back_ts_uid_tid
        btn_user_id = int(data[2])
        t_id = data[3]
        
        if callback_query.from_user.id != btn_user_id:
            return await callback_query.answer("⚠️ الصلاحية منتهية.")

        res = supabase.table("active_trades").select("*").eq("trade_id", t_id).execute()
        if not res.data: 
            return await callback_query.answer("⚠️ الصفقة مغلقة.")
        
        trade = res.data[0]
        coin_res = supabase.table("crypto_market_simulation").select("current_price").eq("symbol", trade['symbol']).execute()
        current_price = int(float(coin_res.data[0]['current_price'])) if coin_res.data else int(float(trade['entry_price']))

        # إرجاع لوحة التحكم الرئيسية بدون توسيع أي قسم
        text, markup = get_trade_settings_view(trade, current_price)
        await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
        await callback_query.answer("🔙 تم الرجوع")
        
    except Exception as e:
        import logging
        logging.error(f"Error in Back TS: {e}")
        await callback_query.answer("❌ خطأ في الرجوع للقائمة.")

# ==========================================
# --- [ نظام التحويلات المالية المطور ] ---
# ==========================================

@dp.callback_query_handler(Text(startswith='transfer_flow:'), state="*")
async def transfer_init(callback_query: types.CallbackQuery, state: FSMContext):
    data = callback_query.data.split(':')
    user_id = int(data[1])
    direction = data[2] # to_bank أو to_wallet
    
    # 🔐 القفل الأمني
    if callback_query.from_user.id != user_id:
        return await callback_query.answer("❌ لا يمكنك التحكم بأموال غيرك!", show_alert=True)
    
    await state.update_data(trans_direction=direction)
    await BankTransfer.waiting_for_amount.set()
    
    # رسائل واضحة تدعم مفهوم الكسور
    prompt = "📥 <b>إيداع للتداول</b>\nأرسل المبلغ المراد تحويله (مثال: 10.50):" if direction == "to_bank" else \
             "📤 <b>سحب للمحفظة</b>\nأرسل المبلغ المراد سحبه (مثال: 5.25):"
    
    await callback_query.message.answer(prompt, parse_mode="HTML")
    await callback_query.answer()

# --- [ 2. معالجة المبلغ وتنفيذ التحديث بدقة float ] ---
@dp.message_handler(state=BankTransfer.waiting_for_amount)
async def process_transfer_amount(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    # 🟢 تحويل المدخل إلى float لدعم الكسور العشرية
    try:
        # تنظيف النص من أي رموز وإدخاله كـ float
        amount_text = message.text.replace(',', '.').replace('$', '').strip()
        amount = round(float(amount_text), 2) # تقريب لرقمين عشريين (سنتات)
        if amount <= 0: raise ValueError
    except:
        return await message.reply("⚠️ يرجى إرسال مبلغ صحيح (أرقام فقط)، مثال: 10.50")

    state_data = await state.get_data()
    direction = state_data.get('trans_direction')
    
    # جلب بيانات المستخدم (استخدام float للأرصدة)
    user_data = await get_user_data(user_id)
    if not user_data: return await state.finish()

    # 🟢 قراءة الأرصدة كـ float
    wallet_bal = float(user_data.get('wallet', 0) or 0)
    bank_bal = float(user_data.get('bank_balance', 0) or 0)

    try:
        if direction == "to_bank":
            if amount > wallet_bal:
                return await message.reply(f"❌ رصيد المحفظة غير كافٍ.\nالمتاح: <code>{wallet_bal:,.2f} $</code>")
            
            # تحديث سوبابيس (بيانات float متوافقة مع numeric)
            supabase.table("users_global_profile").update({
                "wallet": wallet_bal - amount,
                "bank_balance": bank_bal + amount
            }).eq("user_id", user_id).execute()
            
        else: # سحب للمحفظة
            # فحص الهامش المتاح (Margin Check) إذا كان لديه صفقات مفتوحة
            is_safe, health_msg = await check_financial_health(user_id, amount, "WITHDRAW")
            if not is_safe: return await message.reply(health_msg)
            
            if amount > bank_bal:
                return await message.reply(f"❌ رصيد التداول غير كافٍ.\nالمتاح: <code>{bank_bal:,.2f} $</code>")

            supabase.table("users_global_profile").update({
                "bank_balance": bank_bal - amount,
                "wallet": wallet_bal + amount
            }).eq("user_id", user_id).execute()

        await message.answer(f"✅ تم تحويل <b>{amount:,.2f} $</b> بنجاح!", parse_mode="HTML")
        await state.finish()
        
        # تحديث واجهة المحفظة فوراً
        await process_wallet_logic(user_id, message.from_user.first_name, message=message)

    except Exception as e:
        import logging
        logging.error(f"Transfer DB Error: {e}")
        await message.reply("❌ حدث خطأ أثناء التحديث في قاعدة البيانات.")
        await state.finish()
        
# --- قسم القروض ---
@dp.callback_query_handler(Text(startswith='repay_loan:'), state="*")
async def repay_loan_handler(callback_query: types.CallbackQuery):
    try:
        # 🔐 القفل الأمني
        data_parts = callback_query.data.split(':')
        owner_id = int(data_parts[1])
        if callback_query.from_user.id != owner_id:
            return await callback_query.answer("⚠️ لا يمكنك سداد ديون غيرك!", show_alert=True)

        # جلب البيانات مباشرة (float لدعم الكسور)
        res = supabase.table("users_global_profile").select("bank_balance, debt_balance").eq("user_id", owner_id).execute()
        
        if not res.data:
            return await callback_query.answer("❌ لم يتم العثور على بياناتك.", show_alert=True)
            
        user_data = res.data[0]
        debt = float(user_data.get('debt_balance', 0) or 0)
        bank_bal = float(user_data.get('bank_balance', 0) or 0)
        
        if debt <= 0:
            return await callback_query.answer("✅ ليس لديك أي ديون مستحقة حالياً!", show_alert=True)
            
        if bank_bal < debt:
            missing = debt - bank_bal
            return await callback_query.answer(f"❌ رصيد التداول ({bank_bal:,.2f}$) غير كافٍ.\nتحتاج لجمع {missing:,.2f}$ إضافية للسداد.", show_alert=True)
        
        # تنفيذ عملية الخصم (دقة float)
        new_bank_balance = bank_bal - debt
        
        supabase.table("users_global_profile").update({
            "bank_balance": float(new_bank_balance),
            "debt_balance": 0.0
        }).eq("user_id", owner_id).execute()
        
        await callback_query.answer(f"✅ تم سداد القرض بالكامل ({debt:,.2f}$).\nرصيدك الحالي: {new_bank_balance:,.2f}$", show_alert=True)
        
        # تحديث واجهة المحفظة
        await process_wallet_logic(owner_id, callback_query.from_user.first_name, callback=callback_query)

    except Exception as e:
        logging.error(f"❌ Error in repay_loan: {e}")
        await callback_query.answer("⚠️ حدث خطأ فني أثناء السداد.", show_alert=True)
        
@dp.callback_query_handler(Text(startswith='loan_menu:'), state="*")
async def loan_menu(callback_query: types.CallbackQuery):
    # 🔐 القفل الأمني
    owner_id = int(callback_query.data.split(':')[1])
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("⚠️ اطلب قائمة القروض من محفظتك الخاصة!", show_alert=True)
    
    user_data = await get_user_data(owner_id)
    if not user_data: return
    
    current_debt = float(user_data.get('debt_balance', 0) or 0)
    
    if current_debt > 0:
        return await callback_query.answer(f"⚠️ لديك قرض نشط بقيمة {current_debt:,.2f}$، سدده أولاً!", show_alert=True)

    loan_amount = 10000.0  # مبلغ القرض المتاح
    
    markup = InlineKeyboardMarkup()
    # نمرر owner_id في الكولباك للحماية في الخطوة التالية
    markup.add(InlineKeyboardButton(f"💰 اقتراض {loan_amount:,.0f} $ (مرة واحدة)", callback_data=f"exec_loan:{owner_id}:{loan_amount}"))
    markup.add(InlineKeyboardButton("🔙 عودة للمحفظة", callback_data=f"wallet_view:{owner_id}"))
    
    text = (
        f"🏦 | <b>مـركـز الائـتـمـان والـقـروض</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 الـمبلغ الـمتاح لك: <b>{loan_amount:,.2f} $</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"<i>* ملاحظة: القروض تساعدك على بدء التداول عند تصفير المحفظة.</i>"
    )

    await callback_query.message.edit_text(text, reply_markup=markup, parse_mode="HTML")
    
@dp.callback_query_handler(Text(startswith='exec_loan:'), state="*")
async def exec_loan_handler(callback_query: types.CallbackQuery):
    data = callback_query.data.split(':')
    owner_id = int(data[1])
    loan_amount = float(data[2])
    
    # 🔐 تأكيد الهوية
    if callback_query.from_user.id != owner_id:
        return await callback_query.answer("❌ خطأ في التحقق من الهوية!", show_alert=True)
    
    user_data = await get_user_data(owner_id)
    if not user_data: return

    # حساب القيم الجديدة بدقة float
    new_bank = float(user_data.get('bank_balance', 0) or 0) + loan_amount
    new_debt = float(user_data.get('debt_balance', 0) or 0) + loan_amount

    try:
        # تحديث سوبابيس (بيانات float متوافقة مع numeric)
        supabase.table("users_global_profile").update({
            "bank_balance": new_bank,
            "debt_balance": new_debt
        }).eq("user_id", owner_id).execute()
        
        await callback_query.answer(f"✅ تم منحك قرض بقيمة {loan_amount:,.2f} $ بنجاح!", show_alert=True)
        
        # تحديث واجهة المحفظة فوراً
        await process_wallet_logic(owner_id, callback_query.from_user.first_name, callback=callback_query)
        
    except Exception as e:
        logging.error(f"❌ Loan Error: {e}")
        await callback_query.answer("❌ فشل في تحديث قاعدة البيانات، حاول لاحقاً.", show_alert=True)

import asyncio
import aiohttp
import math
import logging  # تمت الإضافة لحل خطأ الـ logging
from datetime import datetime


# تمت إضافة الدالة المفقودة هنا
async def async_manual_upsert(table_name, records):
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates"
    }
    endpoint = f"{SUPABASE_URL}/rest/v1/{table_name}"
    
    # ⏱️ وضع حد زمني ذكي (15 ثانية للاتصال، 30 ثانية للرفع)
    timeout = aiohttp.ClientTimeout(total=45, connect=15)
    
    try:
        # يفضل لاحقاً جعل الـ session عامة (Global)، لكن الآن سنصلحها هكذا:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, json=records, headers=headers) as response:
                if response.status in [200, 201, 204]:
                    return True
                else:
                    error_text = await response.text()
                    logging.error(f"❌ فشل الرفع إلى {table_name}! الحالة: {response.status}")
                    logging.error(f"📝 رسالة الخطأ: {error_text}")
                    return False
    except asyncio.TimeoutError:
        logging.error("⏳ نفد الوقت (Timeout) سوبابيس لم ترد، سيتم التخطي لإكمال الباقي.")
        return False
    except Exception as e:
        logging.error(f"⚠️ خطأ تقني أثناء محاولة الرفع: {str(e)}")
        return False
        
# --- قسم دوال الحساب الرياضي ---
# ==========================================
# --- [ دوال الحساب الرياضي ] ---
# ==========================================
def calculate_ema(data, period):
    if len(data) < period: return data[-1]
    alpha = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price * alpha) + (ema * (1 - alpha))
    return ema
    
def calculate_rsi(series, period: int = 14):
    if isinstance(series, list):
        series = pd.Series(series)
    delta = series.diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/period, adjust=False).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    # نرجع آخر قيمة كرقم صافي بدلاً من Series كاملة
    return float(rsi.values[-1]) if len(rsi) > 0 and not pd.isna(rsi.values[-1]) else 50.0
    

def calculate_bollinger(data, period=20):
    if len(data) < period: return data[-1], data[-1], data[-1]
    recent = data[-period:]
    sma = sum(recent) / period
    variance = sum((x - sma) ** 2 for x in recent) / period
    std_dev = math.sqrt(variance)
    return sma + (std_dev * 2), sma, sma - (std_dev * 2)


def calculate_volume(volumes):
    """
    تعيد حجم التداول للشمعة الحالية (العمود الأخير)
    هذا هو المحرك الذي يكشف دخول السيولة المفاجئ.
    """
    if not volumes: return 0.0
    
    # جلب حجم تداول الشمعة الأخيرة (آخر عمود في الشارت)
    current_volume = float(volumes[-1])
    
    return current_volume
    
def calculate_obv(closes, volumes):
    """
    حساب مؤشر حجم التداول المتوازن (OBV)
    يعتمد على العلاقة بين سعر الإغلاق وحجم التداول
    """
    if len(closes) < 2: return 0.0
    
    obv = 0.0
    # نبدأ الحساب بمقارنة كل شمعة بالتي قبلها
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            # إغلاق صاعد: أضف الفوليوم
            obv += volumes[i]
        elif closes[i] < closes[i-1]:
            # إغلاق هابط: اطرح الفوليوم
            obv -= volumes[i]
        # إذا تساوى الإغلاق يبقى الـ OBV كما هو دون تغيير
            
    return obv

def calculate_bbw(upper, lower, middle):
    """
    تحسب عرض نطاق البولنجر (BBW).
    المعادلة: (الخط العلوي - الخط السفلي) / الخط الأوسط
    """
    try:
        if middle > 0:
            return (upper - lower) / middle
        return 0
    except Exception:
        return 0
  

def calculate_keltner_channels(highs, lows, closes, ema_period=20, atr_period=10, multiplier=2):
    if len(closes) < max(ema_period, atr_period) + 1:
        return closes[-1], closes[-1], closes[-1]
    mid = calculate_ema(closes, ema_period)
    atr_v = calculate_atr(highs, lows, closes, atr_period)
    return mid + (multiplier * atr_v), mid, mid - (multiplier * atr_v)
    
# ==========================================
# --- [ دوال الأدوات المحرمة - قلعة أثر ] ---
# ==========================================

def calculate_atr(highs, lows, closes, period=14):
    """
    نسخة قلعة أثر المعتمدة (Wilder's ATR)
    أدق في حساب الستوب لوز ومنع ضربه بالذيول العشوائية.
    """
    if len(closes) < period + 1: return 0.0
    
    tr_list = []
    for i in range(1, len(closes)):
        # حساب المدى الحقيقي (True Range)
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i-1]),
            abs(lows[i] - closes[i-1])
        )
        tr_list.append(tr)
    
    # حساب أول قيمة كمتوسط بسيط (SMA) لتبدأ منه
    atr = sum(tr_list[:period]) / period
    
    # تطبيق التنعيم (Smoothing) لبقية القيم - هذا هو "سر" الاستقرار
    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        
    return round(atr, 6)

def calculate_adx(highs, lows, closes, period=14):
    """
    قاعدة (المرصاد): حساب مؤشر ADX
    لمعرفة هل العملة في "انفجار" (ADX > 25) أم "تذبذب" (ADX < 20).
    """
    if len(closes) < period * 2: return 0.0
    
    plus_dm = []
    minus_dm = []
    tr_list = []
    
    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        
        plus_dm.append(max(up_move, 0) if up_move > down_move else 0)
        minus_dm.append(max(down_move, 0) if down_move > up_move else 0)
        
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    # حساب الـ DI والـ DX (تبسيطاً للمحرك اليدوي)
    # ملاحظة: هذه نسخة مختصرة لتناسب الأداء السريع في البوت
    avg_tr = sum(tr_list[-period:]) / period
    avg_plus_dm = sum(plus_dm[-period:]) / period
    avg_minus_dm = sum(minus_dm[-period:]) / period
    
    plus_di = 100 * (avg_plus_dm / avg_tr) if avg_tr != 0 else 0
    minus_di = 100 * (avg_minus_dm / avg_tr) if avg_tr != 0 else 0
    
    dx = 100 * abs(plus_di - minus_di) / (plus_di + minus_di) if (plus_di + minus_di) != 0 else 0
    return round(dx, 2)

def calculate_volume_delta(buy_volumes, total_volumes):
    """
    قاعدة (فَتَبَيَّنُوا): حساب صافي السيولة (Volume Delta)
    يميز بين "الزبد" (فوليوم وهمي) و"ما ينفع الناس" (شراء حقيقي).
    """
    if not buy_volumes or not total_volumes: return 0.0
    
    # صافي السيولة = حجم الشراء - حجم البيع (البيع هو الإجمالي ناقص الشراء)
    current_buy = float(buy_volumes[-1])
    current_total = float(total_volumes[-1])
    current_sell = current_total - current_buy
    
    delta = current_buy - current_sell
    return round(delta, 2)



def calculate_macd_values(closes, fast=12, slow=26, signal=9):
    try:
        s = pd.Series(closes)
        ema_fast = s.ewm(span=fast, adjust=False).mean()
        ema_slow = s.ewm(span=slow, adjust=False).mean()
        macd_line = ema_fast - ema_slow
        signal_line = macd_line.ewm(span=signal, adjust=False).mean()
        histogram = macd_line - signal_line
        
        # التأكد التام من تحويلها لأرقام بايثون الصافية
        return {
            "macd": float(macd_line.values[-1]) if len(macd_line) > 0 else 0.0,
            "signal": float(signal_line.values[-1]) if len(signal_line) > 0 else 0.0,
            "hist": float(histogram.values[-1]) if len(histogram) > 0 else 0.0
        }
    except Exception as e:
        print(f"❌ خطأ في الحساب اليدوي للماكد: {e}")
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}


# ==========================================
# 2. محرك الهارمونيك المطور (EliteTradingEngine)
# ==========================================
def calculate_mfi(highs, lows, closes, volumes, period=14):
    """مؤشر تدفق الأموال (MFI): يقيس ضغط الشراء/البيع بدمج السعر مع الحجم"""
    if len(closes) < period + 1: return 50.0
    typical_price = (np.array(highs) + np.array(lows) + np.array(closes)) / 3
    raw_money_flow = typical_price * np.array(volumes)
    
    pos_flow, neg_flow = [], []
    for i in range(1, len(typical_price)):
        if typical_price[i] > typical_price[i-1]:
            pos_flow.append(raw_money_flow[i])
            neg_flow.append(0.0)
        else:
            pos_flow.append(0.0)
            neg_flow.append(raw_money_flow[i])
            
    pos_sum = sum(pos_flow[-period:])
    neg_sum = sum(neg_flow[-period:])
    
    if neg_sum == 0: return 100.0
    mfi = 100 - (100 / (1 + (pos_sum / neg_sum)))
    return round(mfi, 2)

def calculate_cmf(highs, lows, closes, volumes, period=20):
    """مؤشر تشايكين (CMF): يكشف تجميع الحيتان (فوق 0) أو تصريفهم (تحت 0)"""
    if len(closes) < period: return 0.0
    h, l, c, v = np.array(highs), np.array(lows), np.array(closes), np.array(volumes)
    
    divisor = h - l
    # حماية من القسمة على صفر
    divisor = np.where(divisor == 0, 0.0001, divisor)
    mfm = ((c - l) - (h - c)) / divisor
    mfv = mfm * v
    
    cmf = sum(mfv[-period:]) / sum(v[-period:]) if sum(v[-period:]) > 0 else 0.0
    return round(cmf, 4)

def calculate_vwap_and_distance(highs, lows, closes, volumes, current_price):
    """حساب VWAP المرجح بالحجم ومسافة السعر عنه"""
    if len(closes) == 0 or sum(volumes) == 0: return 0.0, 0.0
    typical_price = (np.array(highs) + np.array(lows) + np.array(closes)) / 3
    vwap = np.sum(typical_price * np.array(volumes)) / np.sum(volumes)
    distance_pct = ((current_price - vwap) / vwap) * 100 if vwap > 0 else 0.0
    return round(vwap, 5), round(distance_pct, 2)

def calculate_volume_profile(closes, volumes, bins=20):
    """رسم مبسط لبروفايل الحجم (POC, VAH, VAL)"""
    if len(closes) < bins: return 0.0, 0.0, 0.0
    
    df_vp = pd.DataFrame({'close': closes, 'volume': volumes})
    # تقسيم الأسعار إلى مستويات (Bins)
    df_vp['price_bin'] = pd.cut(df_vp['close'], bins=bins)
    vp = df_vp.groupby('price_bin')['volume'].sum().reset_index()
    
    # نقطة التحكم (أعلى فوليوم)
    poc_idx = vp['volume'].idxmax()
    poc_price = vp.iloc[poc_idx]['price_bin'].mid
    
    # حساب منطقة القيمة (70% من الفوليوم) بشكل مبسط (Upper & Lower)
    total_vol = vp['volume'].sum()
    vah_price = vp['price_bin'].apply(lambda x: x.right).quantile(0.85) # تقدير تقريبي
    val_price = vp['price_bin'].apply(lambda x: x.left).quantile(0.15)  # تقدير تقريبي
    
    return round(poc_price, 5), round(vah_price, 5), round(val_price, 5)
    
def calculate_stochastic(highs, lows, closes, period=14, smooth_k=3):
    """الاستوكاستيك (Stochastic K & D)"""
    if len(closes) < period + smooth_k: return 50.0, 50.0
    h, l, c = np.array(highs), np.array(lows), np.array(closes)
    
    highest_high = pd.Series(h).rolling(window=period).max()
    lowest_low = pd.Series(l).rolling(window=period).min()
    
    k_raw = 100 * ((pd.Series(c) - lowest_low) / (highest_high - lowest_low))
    k_raw = k_raw.fillna(50)
    
    stoch_k = k_raw.rolling(window=smooth_k).mean().iloc[-1]
    stoch_d = k_raw.rolling(window=smooth_k).mean().rolling(window=3).mean().iloc[-1]
    return round(stoch_k, 2), round(stoch_d, 2)

def calculate_williams_r(highs, lows, closes, period=14):
    """ويليامز %R (متخصص في اصطياد القمم والقيعان السريعة)"""
    if len(closes) < period: return -50.0
    highest_high = max(highs[-period:])
    lowest_low = min(lows[-period:])
    if highest_high == lowest_low: return -50.0
    w_r = -100 * ((highest_high - closes[-1]) / (highest_high - lowest_low))
    return round(w_r, 2)

def calculate_choppiness_index(highs, lows, closes, period=14):
    """مؤشر التذبذب المزعج: فوق 61 = تذبذب قاتل، تحت 38 = ترند قوي"""
    if len(closes) < period + 1: return 50.0
    tr_sum = sum([max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1])) for i in range(len(closes)-period, len(closes))])
    highest_high = max(highs[-period:])
    lowest_low = min(lows[-period:])
    
    if highest_high - lowest_low == 0: return 50.0
    chop = 100 * np.log10(tr_sum / (highest_high - lowest_low)) / np.log10(period)
    return round(chop, 2)
    
def calculate_ichimoku(highs, lows):
    """سحابة إيشيموكو (Ichimoku Cloud) الأساسية"""
    if len(highs) < 52: return None, None, None, None
    h, l = np.array(highs), np.array(lows)
    
    tenkan = (max(h[-9:]) + min(l[-9:])) / 2
    kijun = (max(h[-26:]) + min(l[-26:])) / 2
    senkou_a = (tenkan + kijun) / 2
    senkou_b = (max(h[-52:]) + min(l[-52:])) / 2
    
    return round(tenkan, 5), round(kijun, 5), round(senkou_a, 5), round(senkou_b, 5)

def calculate_supertrend_psar(df, period=10, multiplier=3):
    """دالة مدمجة مبسطة تحاكي قوة السوبر ترند"""
    if len(df) < period + 1: return 0.0, 0.0
    # سوبر ترند تقريبي باستخدام الـ ATR
    atr = calculate_atr(df['high'].values, df['low'].values, df['close'].values, period)
    hl2 = (df['high'].iloc[-1] + df['low'].iloc[-1]) / 2
    supertrend_val = hl2 - (multiplier * atr) if df['close'].iloc[-1] > df['close'].iloc[-2] else hl2 + (multiplier * atr)
    
    # البارابوليك سار (نأخذ أدنى نقطة حديثة كقيمة تقريبية للسار في الترند الصاعد)
    psar_val = df['low'].rolling(5).min().iloc[-2] if df['close'].iloc[-1] > df['close'].iloc[-2] else df['high'].rolling(5).max().iloc[-2]
    
    return round(supertrend_val, 5), round(psar_val, 5)
    
def calculate_pivot_points(high_prev, low_prev, close_prev):
    """النقاط المحورية القياسية بناءً على الشمعة السابقة (اليومية عادة)"""
    p = (high_prev + low_prev + close_prev) / 3
    r1 = (2 * p) - low_prev
    s1 = (2 * p) - high_prev
    return round(p, 5), round(r1, 5), round(s1, 5)

def get_last_fractals(highs, lows):
    """آخر قمة وقاع فركتال (Fractal)"""
    if len(highs) < 5: return 0.0, 0.0
    last_high_fractal = 0.0
    last_low_fractal = 0.0
    
    # البحث من النهاية للبداية عن آخر تشكيل فركتال
    for i in range(len(highs)-3, 1, -1):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            last_high_fractal = highs[i]
            break
            
    for i in range(len(lows)-3, 1, -1):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            last_low_fractal = lows[i]
            break
            
    return round(last_high_fractal, 5), round(last_low_fractal, 5)

def calculate_linreg_curve(closes, period=20):
    """خط الانحدار الخطي لنهاية السعر (مغناطيس الترند)"""
    if len(closes) < period: return closes[-1]
    y = np.array(closes[-period:])
    x = np.arange(len(y))
    slope, intercept, _, _, _ = linregress(x, y)
    linreg_val = (slope * (len(y) - 1)) + intercept
    return round(linreg_val, 5)

def calculate_volume_oscillator(volumes, short_period=14, long_period=28):
    """مذبذب الحجم (Volume Oscillator): يقيس الفرق النسبي بين متوسطين للحجم لكشف ضخ السيولة"""
    if len(volumes) < long_period: return 0.0
    
    vol_series = pd.Series(volumes)
    short_ma = vol_series.rolling(window=short_period).mean().iloc[-1]
    long_ma = vol_series.rolling(window=long_period).mean().iloc[-1]
    
    if long_ma == 0: return 0.0
    vol_osc = ((short_ma - long_ma) / long_ma) * 100
    return round(vol_osc, 2)
    

async def fetch_klines(session, symbol, interval, limit=100):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}"
    try:
        async with session.get(url, timeout=10) as res:
            if res.status == 200: return await res.json()
    except: return None


async def update_crypto_market_data():
    print(f"\n🚀 {datetime.now().strftime('%H:%M:%S')} | بدء جلب بيانات Binance Vision (نطاق التحليل الفني والسيولة)...")
    
    async with aiohttp.ClientSession() as session:
        # ✨ جلب حالة العملات الحية أولاً لتصفية الميتة والمتوقفة
        valid_symbols = set()
        try:
            async with session.get("https://data-api.binance.vision/api/v3/exchangeInfo", timeout=10) as ex_res:
                if ex_res.status == 200:
                    ex_data = await ex_res.json()
                    for s in ex_data.get('symbols', []):
                        if s['status'] == 'TRADING' and s['quoteAsset'] == 'USDT':
                            valid_symbols.add(s['symbol'])
        except Exception as e:
            logging.error(f"❌ فشل جلب ExchangeInfo: {e}")
            return

        try:
            async with session.get("https://data-api.binance.vision/api/v3/ticker/24hr", timeout=10) as res:
                if res.status != 200: return
                ticker_data = await res.json()
                if not isinstance(ticker_data, list): return
        except Exception as e:
            logging.error(f"❌ فشل الاتصال بـ API التيكر: {e}")
            return

        STABLE_COINS = {
            "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "BUSDUSDT", 
            "DAIUSDT", "EURUSDT", "AEURUSDT", "USDPUSDT", "USDDUSDT",
            "PYUSDUSDT", "EURIUSDT"
        }

        top_coins = []
        for c in ticker_data:
            if not isinstance(c, dict): continue
            
            symbol = c.get('symbol', '')
            
            if symbol not in valid_symbols: continue
            if not symbol.endswith('USDT'): continue
            if symbol in STABLE_COINS: continue 
            if symbol.endswith('UPUSDT') or symbol.endswith('DOWNUSDT'): continue 
            
            last_price = float(c.get('lastPrice', 0))
            quote_volume = float(c.get('quoteVolume', 0))
            high_price = float(c.get('highPrice', 0))
            low_price = float(c.get('lowPrice', 0))
            trades_count = int(c.get('count', 0))

            if last_price < 0.0001: continue
            
            if 0.98 <= last_price <= 1.02 and low_price > 0:
                price_volatility = (high_price - low_price) / low_price
                if price_volatility < 0.015: 
                    continue 
                    
            if trades_count < 1000: continue
            if quote_volume < 100000: continue
            if high_price == low_price: continue
            
            top_coins.append(c)
        
        top_coins = sorted(top_coins, key=lambda x: float(x.get('quoteVolume', 0)), reverse=True)[:200]
        
        # تم تحديد الفريمات المطلوبة فقط لتوفير الموارد وتسريع الجلب
        timeframes = ['15m', '1h', '2h', '4h']
        final_records = []

        for coin in top_coins:
            symbol = coin.get('symbol')
            try:
                price = float(coin.get('lastPrice', 0))
                change_percent = float(coin.get('priceChangePercent', 0))
                
                # جلب عمق السوق
                orderbook_url = f"https://data-api.binance.vision/api/v3/depth?symbol={symbol}&limit=20"
                imbalance_ratio = 1.0 
                
                try:
                    async with session.get(orderbook_url, timeout=5) as ob_res:
                        if ob_res.status == 200:
                            depth = await ob_res.json()
                            bids_vol = sum([float(bid[1]) for bid in depth.get('bids', [])])
                            asks_vol = sum([float(ask[1]) for ask in depth.get('asks', [])])
                            if asks_vol > 0:
                                imbalance_ratio = bids_vol / asks_vol
                except Exception as e:
                    logging.warning(f"⚠️ فشل جلب عمق السوق لـ {symbol}: {e}")

                # إعداد السجل الأساسي (المشترك)
                record = {
                    "symbol": symbol,
                    "name": symbol.replace("USDT", ""),
                    "current_price": price,
                    "open_price_24h": float(coin.get('openPrice', 0)),
                    "high_24h": float(coin.get('highPrice', 0)),
                    "low_24h": float(coin.get('lowPrice', 0)),
                    "volume_24h": float(coin.get('volume', 0)),
                    "change_24h": change_percent,
                    "last_tick_direction": "UP" if change_percent >= 0 else "DOWN",
                    "updated_at": datetime.now().isoformat(),
                    "last_api_update_ms": int(datetime.now().timestamp() * 1000),
                    "orderbook_imbalance_ratio": round(imbalance_ratio, 4)
                }
                
                tasks = [fetch_klines(session, symbol, tf) for tf in timeframes]
                results = await asyncio.gather(*tasks)

                for i, tf in enumerate(timeframes):
                    if results[i] and isinstance(results[i], list):
                        df_tf = pd.DataFrame(results[i], columns=[
                            'timestamp', 'open', 'high', 'low', 'close', 'volume',
                            'close_time', 'quote_av', 'trades', 'tb_base_av', 'tb_quote_av', 'ignore'
                        ])
                        
                        for col in ['open', 'high', 'low', 'close', 'volume', 'tb_base_av']:
                            df_tf[col] = df_tf[col].astype(float)

                        highs = df_tf['high'].tolist()
                        lows = df_tf['low'].tolist()
                        closes = df_tf['close'].tolist()
                        volumes = df_tf['volume'].tolist()
                        taker_buy_vols = [float(k[9]) for k in results[i]]

                        # ==========================================
                        # 🟢 فريم 15 دقيقة (مؤشرات السكالبينج والطرد)
                        # ==========================================
                        if tf == '15m':
                            upper, mid, lower = calculate_bollinger(closes)
                            bbw_val = (upper - lower) / mid if mid > 0 else 0
                            macd_data = calculate_macd_values(closes)
                            rsi_series = calculate_rsi(closes)
                            kc_up, kc_mid, kc_low = calculate_keltner_channels(highs, lows, closes)
                            stoch_k, stoch_d = calculate_stochastic(highs, lows, closes)
                            obv_val = calculate_obv(closes, volumes)
                            obv_prev_val = calculate_obv(closes[:-1], volumes[:-1]) if len(closes) > 1 else 0.0
                            supertrend, psar = calculate_supertrend_psar(df_tf)
                            vwap_val, _ = calculate_vwap_and_distance(highs, lows, closes, volumes, closes[-1])

                            record.update({
                                "volume_15m": float(volumes[-1]),
                                "volume_ma_15m": sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes)/len(volumes),
                                "bbw_15m": bbw_val,
                                "bb_upper_15m": upper,
                                "bb_lower_15m": lower,
                                "adx_15m": calculate_adx(highs, lows, closes),
                                "macd_15m": macd_data['macd'],
                                "macd_signal_15m": macd_data['signal'],
                                "macd_hist_15m": macd_data['hist'],
                                "ema_20_15m": calculate_ema(closes, 20),
                                "ema_50_15m": calculate_ema(closes, 50),
                                "ema_100_15m": calculate_ema(closes, 100),
                                "rsi_15m": float(rsi_series.iloc[-1]) if hasattr(rsi_series, 'iloc') and len(rsi_series) > 0 else 50.0,
                                "mfi_15m": calculate_mfi(highs, lows, closes, volumes),
                                "supertrend_15m": supertrend,
                                "stochastic_k_15m": stoch_k,
                                "stochastic_d_15m": stoch_d,
                                "obv_15m": obv_val,
                                "obv_slope_15m": obv_val - obv_prev_val,
                                "cmf_15m": calculate_cmf(highs, lows, closes, volumes),
                                "williams_r_15m": calculate_williams_r(highs, lows, closes),
                                "choppiness_index_15m": calculate_choppiness_index(highs, lows, closes),
                                "parabolic_sar_15m": psar,
                                "volume_delta_15m": calculate_volume_delta(taker_buy_vols, volumes),
                                "kc_upper_15m": kc_up,
                                "kc_lower_15m": kc_low,
                                "vwap_15m": vwap_val
                            })

                        # ==========================================
                        # 🔵 فريم 1 ساعة (الاتجاه العام والحيتان ومناطق القيمة)
                        # ==========================================
                        elif tf == '1h':
                            # حساب بصمات الحيتان
                            point_zero_idx = len(df_tf) - 1 
                            taker_buy_ratio = 1.0
                            whale_net_flow = 0.0
                            
                            if point_zero_idx >= 1:
                                tbv_before = float(df_tf.iloc[point_zero_idx - 1]['tb_base_av']) 
                                total_vol_before = float(df_tf.iloc[point_zero_idx - 1]['volume'])
                                tsv_before = total_vol_before - tbv_before 
                                taker_buy_ratio = (tbv_before / tsv_before) if tsv_before > 0 else 1.0
                                whale_net_flow = tbv_before - tsv_before 

                            upper, mid, lower = calculate_bollinger(closes)
                            rsi_series = calculate_rsi(closes)
                            vwap_val, _ = calculate_vwap_and_distance(highs, lows, closes, volumes, closes[-1])
                            kc_up, kc_mid, kc_low = calculate_keltner_channels(highs, lows, closes)
                            supertrend, psar = calculate_supertrend_psar(df_tf)
                            stoch_k, _ = calculate_stochastic(highs, lows, closes)
                            obv_val = calculate_obv(closes, volumes)
                            obv_prev_val = calculate_obv(closes[:-1], volumes[:-1]) if len(closes) > 1 else 0.0
                            tenkan, kijun, senkou_a, senkou_b = calculate_ichimoku(highs, lows)
                            poc_price, vah_price, val_price = calculate_volume_profile(closes, volumes)

                            record.update({
                                "taker_buy_ratio_1h": float(taker_buy_ratio),
                                "whale_net_flow_volume": float(whale_net_flow),
                                "whale_absorption_detected": False,
                                "volume_1h": float(volumes[-1]),
                                "volume_ma_1h": sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes)/len(volumes),
                                "ema_20_1h": calculate_ema(closes, 20),
                                "ema_200_1h": calculate_ema(closes, 200),
                                "rsi_1h": float(rsi_series.iloc[-1]) if hasattr(rsi_series, 'iloc') and len(rsi_series) > 0 else 50.0,
                                "adx_1h": calculate_adx(highs, lows, closes),
                                "bb_upper_1h": upper,
                                "bb_lower_1h": lower,
                                "vwap_1h": vwap_val,
                                "volume_delta_1h": calculate_volume_delta(taker_buy_vols, volumes),
                                "williams_r_1h": calculate_williams_r(highs, lows, closes),
                                "supertrend_1h": supertrend,
                                "kc_upper_1h": kc_up,
                                "kc_lower_1h": kc_low,
                                "cmf_1h": calculate_cmf(highs, lows, closes, volumes),
                                "stochastic_k_1h": stoch_k,
                                "choppiness_index_1h": calculate_choppiness_index(highs, lows, closes),
                                "obv_slope_1h": obv_val - obv_prev_val,
                                "parabolic_sar_1h": psar,
                                "mfi_1h": calculate_mfi(highs, lows, closes, volumes),
                                "ichimoku_conversion_1h": tenkan,
                                "ichimoku_base_1h": kijun,
                                "ichimoku_cloud_top_1h": senkou_a,
                                "ichimoku_cloud_bottom_1h": senkou_b,
                                "value_area_high_1h": vah_price,
                                "value_area_low_1h": val_price,
                                "poc_price_1h": poc_price
                            })

                        # ==========================================
                        # 🟠 فريم 2 ساعة (دعم الاتجاه والسيولة)
                        # ==========================================
                        elif tf == '2h':
                            _, psar = calculate_supertrend_psar(df_tf)
                            record.update({
                                "volume_delta_2h": calculate_volume_delta(taker_buy_vols, volumes),
                                "parabolic_sar_2h": psar
                            })

                        # ==========================================
                        # 🟣 فريم 4 ساعات (نظرة الماكرو للاتجاه)
                        # ==========================================
                        elif tf == '4h':
                            vwap_val, _ = calculate_vwap_and_distance(highs, lows, closes, volumes, closes[-1])
                            record.update({
                                "ema_20_4h": calculate_ema(closes, 20),
                                "vwap_4h": vwap_val
                            })

                final_records.append(record)
                print(f"🔹 [فحص] تم تجهيز {symbol} بجميع الفريمات بكفاءة") 
            except Exception as e: 
                logging.error(f"❌ خطأ في معالجة {symbol}: {e}")
                continue

        print(f"📊 إجمالي العملات الجاهزة للرفع: {len(final_records)}")

        if final_records:
            print(f"📦 جاري رفع {len(final_records)} عملة إلى سوبابيس...")
            for i in range(0, len(final_records), 50): 
                batch = final_records[i:i + 50]
                success = await async_manual_upsert("crypto_market_simulation_u", batch)
                
                if success:
                    logging.info(f"✅ تم حقن الدفعة {i//50 + 1} بنجاح")
                else:
                    logging.error(f"⚠️ فشل في حقن الدفعة {i//50 + 1}")
                
                await asyncio.sleep(1)

        print(f"🏁 {datetime.now().strftime('%H:%M:%S')} | انتهت دورة التحديث بالكامل.")
        print(f"🏁 {datetime.now().strftime('%H:%M:%S')} | انتهت مهمة السكربت، الرادار يتولى الآن.")


async def unified_trading_system():
    """
    المايسترو المطور v11.3: يعمل بنظام العداد الزمني المستقل
    يحدث البيانات 👈 يطلق الرادار الأول 👈 ينتظر 5 ثوانٍ 👈 يطلق الرادار الثاني 👈 ثم ينتظر بالعداد الزمني المحدّد.
    """
    logging.info("✅ بدء تشغيل المايسترو بنظام العداد الزمني المستقل...")
    
    while True:
        try:
            print(f"\n⚡ [دورة جديدة للرادار]: بدء التحديث والمسح... {datetime.now().strftime('%H:%M:%S')}")
            
            # 🔄 المرحلة الأولى: استدعاء دالة تحديث بيانات السوق والمؤشرات
            print("📥 جاري تشغيل دالة تحديث بيانات السوق والمؤشرات (update_crypto_market_data)...")
            await update_crypto_market_data()

            # ⏳ العداد الزمني: انتظر دقيقة واحدة (60 ثانية) قبل بدء الدورة التالية تلقائياً
            print("🏁 اكتملت الدورة بالكامل بنجاح. العداد الزمني ينطلق الآن... انتظر 60 ثانية.")
            await asyncio.sleep(5)  # 👈 يمكنك تعديل الـ 60 ثانية لأي وقت تراه مناسباً

        except Exception as e:
            logging.error(f"🚨 خطأ في المايسترو: {e}")
            await asyncio.sleep(10)
            

# 1. 🟢 ضع هذا الكلاس قبل "نظام الإنعاش الأبدي" (في منطقة عامة خارج الدوال)
class TelegramLoggerHandler(logging.Handler):
    def __init__(self, bot, chat_id):
        super().__init__()
        self.bot = bot
        self.chat_id = chat_id

    def emit(self, record):
        log_entry = self.format(record)
        if record.levelno >= logging.ERROR:
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self.send_log(log_entry))
            except RuntimeError:
                pass

    async def send_log(self, message):
        try:
            msg = f"⚠️ <b>تنبيـه خطأ في النظام:</b>\n<code>{message[:3500]}</code>"
            await self.bot.send_message(self.chat_id, msg, parse_mode="HTML")
        except Exception:
            pass

# ==========================================
# 5. نهاية الملف: نظام الإنعاش الأبدي 24/7 (النبض الذاتي) ⚡
# ==========================================
import os
import asyncio
import logging
import random
import aiohttp
from aiohttp import web

# ==========================================
# 5. نظام الإنعاش الأبدي: "لا تأخذه سنة ولا نوم" ⚡
# ==========================================
async def sync_and_error_bridge():
    """
    الجسر المطور: يفحص الأخطاء، ويرسل الإشعارات، ويتأكد من الدور.
    تمت إضافة نظام معالجة الجلسات المغلقة (Session Fix).
    """
    headers = {
        "apikey": SUPABASE_KEY, 
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json"
    }
    
    try:
        # ✅ فتح جلسة جديدة لكل محاولة لضمان عدم حدوث Session is closed
        async with aiohttp.ClientSession() as session:
            
            # [1] جلب الأخطاء الجديدة من السكربت
            error_url = f"{SUPABASE_URL}/rest/v1/script_errors?is_reported=eq.false"
            async with session.get(error_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    errors = await resp.json()
                    for err in errors:
                        alert = f"⚠️ <b>تنبيه من السكربت الخارجي:</b>\n<code>{err['error_message']}</code>"
                        try:
                            await bot.send_message(GROUP_ID, alert, parse_mode="HTML")
                            # تحديث الحالة إلى "تم التبليغ"
                            update_url = f"{SUPABASE_URL}/rest/v1/script_errors?id=eq.{err['id']}"
                            await session.patch(update_url, json={"is_reported": True}, headers=headers)
                        except Exception as telegram_err:
                            logging.error(f"❌ فشل إرسال تنبيه تلجرام: {telegram_err}")

            # [2] تنظيف الأخطاء القديمة (اختياري لتوفير المساحة)
            delete_url = f"{SUPABASE_URL}/rest/v1/script_errors?is_reported=eq.true"
            await session.delete(delete_url, headers=headers)

            # [3] فحص من عليه الدور الآن؟
            sync_url = f"{SUPABASE_URL}/rest/v1/system_sync?id=eq.1"
            async with session.get(sync_url, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    sync_data = await resp.json()
                    if sync_data:
                        return sync_data[0]['current_turn']
                else:
                    logging.warning(f"⚠️ فشل جلب الدور من سوبابيس، كود الحالة: {resp.status}")

    except aiohttp.ClientError as e:
        logging.error(f"🌐 خطأ في اتصال الشبكة: {e}")
    except Exception as e:
        logging.error(f"⚠️ خطأ غير متوقع في جسر التنسيق: {e}")
    
    return "wait" # في حالة أي خلل، نطلب من المايسترو الانتظار


async def handle_ping(request):
    """استجابة سريعة لإخبار السيرفر أن النظام مستيقظ"""
    return web.Response(
        text="Alive & Vigilant ⚡", 
        headers={"Connection": "keep-alive"}
    )


async def handle_telegram_login(request):
    return web.Response(text="✅ Data Received")


async def self_resuscitation():
    """النبض الذاتي: البوت يوقظ نفسه لمنع النوم (Anti-Idle)"""
    render_url = os.getenv("RENDER_EXTERNAL_URL") 
    if not render_url: return

    while True:
        try:
            # كسر التخزين المؤقت لضمان وصول الطلب للمعالج مباشرة
            rand_ping = f"{render_url}?v={random.randint(1, 99999)}"
            async with aiohttp.ClientSession() as session:
                async with session.get(rand_ping, timeout=10) as response:
                    logging.info(f"💉 [نبضة حية]: {response.status}")
        except Exception as e:
            logging.error(f"⚠️ [فشل النبض]: {e}")
        
        await asyncio.sleep(240) # كل 4 دقائق


async def watch_dog(task_func, *args):
    """
    بروتوكول اليقظة: مراقب دائم للمحركات.
    إذا توقف أي محرك (سنة) أو انهار (نوم)، يعيده للحياة فوراً.
    """
    while True:
        try:
            logging.info(f"🛡️ تشغيل محرك: {task_func.__name__}")
            await task_func(*args)
        except Exception as e:
            logging.error(f"🚨 انهيار في {task_func.__name__}: {e}")
            logging.info("♻️ إعادة التشغيل التلقائي الآن...")
            await asyncio.sleep(10) # انتظار بسيط لتجنب التكرار السريع عند الخطأ


async def auto_evaluation_scheduler():
    """
    مجدول زمني شبحي يعمل في الخلفية لتقييم الصفقات كل 12 ساعة.
    """
    while True:
        try:
            print(f"🔄 [مجدول التقييم] بدء فحص الإشارات القديمة في: {datetime.now().strftime('%H:%M:%S')}")
            await evaluate_old_signals()
        except Exception as e:
            print(f"⚠️ خطأ في المجدول الزمني: {e}")
        
        # النوم لمدة 12 ساعة (بثواني) قبل الفحص التالي
        await asyncio.sleep(12 * 60 * 60)


    # ... تشغيل polling التلجرام ...
# ---async def main_startup ---
async def main_startup():
    # 2. 🟢 ضع هذا الإعداد هنا في أول سطر داخل دالة main_startup
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(), # للطباعة في شاشة راندر كالعادة
            TelegramLoggerHandler(bot, GROUP_ID) # ليرسل الأخطاء للقروب فوراً
        ]
    )

    # أ) إعداد سيرفر الويب للبقاء Online (مهم للمنصات مثل Render/Heroku)
    app = web.Application()
    app.router.add_get('/', handle_ping)
    app.router.add_get('/login', handle_telegram_login)
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 10000))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    logging.info(f"🌐 Server Active on port {port}")


    # ب) تشغيل المحركات تحت حماية الـ WatchDog
    asyncio.create_task(watch_dog(unified_trading_system))
    asyncio.create_task(watch_dog(self_resuscitation))
    
    # ج) تشغيل البوت الرئيسي (Aiogram) مع نظام إعادة المحاولة الصامد
    while True:
        try:
            logging.info("🚀 إقلاع محرك التليجرام... النظام تحت الحماية القصوى.")
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as e:
            logging.error(f"❌ خطأ في البوت: {e}")
            logging.info("🔄 محاولة إعادة التشغيل تلقائياً خلال 10 ثوانٍ...")
            await asyncio.sleep(10)
    
# ---if __name__ == '__main__':---
if __name__ == '__main__':
    try:
        # تشغيل المحرك الرئيسي
        
        asyncio.run(main_startup())
    except KeyboardInterrupt:
        print("🛑 تم إيقاف النظام يدوياً من قبل أثير.")
    except Exception as e:
        # 🟢 طباعة إجبارية باللون الأحمر في راندر لكشف الخطأ القاتل
        print("\n" + "❌"*20)
        print(f"💥 انهيار قاتل منع البوت من الإقلاع:")
        print(f"{type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()
        print("❌"*20 + "\n")
        
        logging.critical(f"💥 انهيار غير متوقع في النظام: {e}")
