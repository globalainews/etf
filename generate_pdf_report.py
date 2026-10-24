# -*- coding: utf-8 -*-
"""生成ETF基金规模趋势分析PDF报告"""
import os
import sys
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.colors import (
    HexColor, white, black, grey, lightgrey
)
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus.flowables import Flowable
import datetime

# ── 注册中文字体 ──
FONT_PATHS = [
    ('C:/Windows/Fonts/msyh.ttc', 'MSYH'),
    ('C:/Windows/Fonts/msyhbd.ttc', 'MSYHBD'),
    ('C:/Windows/Fonts/simhei.ttf', 'SIMHEI'),
    ('C:/Windows/Fonts/simsun.ttc', 'SIMSUN'),
]

font_registered = False
for path, name in FONT_PATHS:
    if os.path.exists(path):
        try:
            pdfmetrics.registerFont(TTFont(name, path, subfontIndex=0))
            font_registered = True
            print(f"已注册字体: {name} from {path}")
        except Exception as e:
            try:
                pdfmetrics.registerFont(TTFont(name, path))
                font_registered = True
                print(f"已注册字体: {name} from {path}")
            except Exception as e2:
                print(f"字体注册失败 {path}: {e2}")

if not font_registered:
    print("⚠ 未找到中文字体，尝试其他路径...")
    import glob
    for f in glob.glob('C:/Windows/Fonts/*.ttf') + glob.glob('C:/Windows/Fonts/*.ttc'):
        try:
            name = os.path.splitext(os.path.basename(f))[0].upper().replace(' ', '_')[:20]
            pdfmetrics.registerFont(TTFont(name, f, subfontIndex=0))
            print(f"已注册: {name}")
            font_registered = True
            break
        except:
            pass

# 确定使用的中文字体
CN_FONT = 'MSYH'
CN_FONT_BOLD = 'MSYHBD' if os.path.exists('C:/Windows/Fonts/msyhbd.ttc') else CN_FONT

# ── 颜色方案 ──
PRIMARY = HexColor('#1a3a5c')
SECONDARY = HexColor('#2d6aa0')
ACCENT = HexColor('#e65c00')
ACCENT_GREEN = HexColor('#2e7d32')
ACCENT_RED = HexColor('#c62828')
BG_LIGHT = HexColor('#f5f8fc')
BG_HEADER = HexColor('#e8f0fa')
TABLE_HEADER_BG = HexColor('#1a3a5c')
TABLE_ROW_ALT = HexColor('#f8fafd')
BORDER_COLOR = HexColor('#d0d0d0')
DARK_TEXT = HexColor('#333333')
MID_TEXT = HexColor('#666666')
LIGHT_TEXT = HexColor('#999999')

# ── 页面设置 ──
PAGE_W, PAGE_H = A4  # 210 x 297 mm

# ── 样式 ──
styles = getSampleStyleSheet()

style_title = ParagraphStyle(
    'CN_Title', fontName=CN_FONT_BOLD, fontSize=22, leading=30,
    textColor=PRIMARY, alignment=TA_CENTER, spaceAfter=4*mm
)
style_subtitle = ParagraphStyle(
    'CN_Subtitle', fontName=CN_FONT, fontSize=10, leading=14,
    textColor=MID_TEXT, alignment=TA_CENTER, spaceAfter=8*mm
)
style_h1 = ParagraphStyle(
    'CN_H1', fontName=CN_FONT_BOLD, fontSize=16, leading=22,
    textColor=PRIMARY, spaceBefore=8*mm, spaceAfter=4*mm
)
style_h2 = ParagraphStyle(
    'CN_H2', fontName=CN_FONT_BOLD, fontSize=13, leading=18,
    textColor=SECONDARY, spaceBefore=5*mm, spaceAfter=3*mm
)
style_body = ParagraphStyle(
    'CN_Body', fontName=CN_FONT, fontSize=9.5, leading=15,
    textColor=DARK_TEXT, alignment=TA_JUSTIFY, spaceAfter=2*mm,
    firstLineIndent=0
)
style_body_small = ParagraphStyle(
    'CN_BodySmall', fontName=CN_FONT, fontSize=8.5, leading=12,
    textColor=DARK_TEXT, spaceAfter=1*mm
)
style_table_header = ParagraphStyle(
    'CN_TH', fontName=CN_FONT_BOLD, fontSize=8.5, leading=12,
    textColor=white, alignment=TA_CENTER
)
style_table_cell = ParagraphStyle(
    'CN_TD', fontName=CN_FONT, fontSize=8.5, leading=12,
    textColor=DARK_TEXT, alignment=TA_CENTER
)
style_table_cell_left = ParagraphStyle(
    'CN_TD_L', fontName=CN_FONT, fontSize=8.5, leading=12,
    textColor=DARK_TEXT, alignment=TA_LEFT
)
style_highlight = ParagraphStyle(
    'CN_Highlight', fontName=CN_FONT_BOLD, fontSize=10, leading=15,
    textColor=ACCENT, spaceAfter=2*mm
)
style_footer = ParagraphStyle(
    'CN_Footer', fontName=CN_FONT, fontSize=7.5, leading=10,
    textColor=LIGHT_TEXT, alignment=TA_CENTER
)
style_caption = ParagraphStyle(
    'CN_Caption', fontName=CN_FONT, fontSize=8, leading=11,
    textColor=MID_TEXT, alignment=TA_LEFT, spaceAfter=3*mm
)


def P(text, style=style_body):
    """快捷创建段落"""
    return Paragraph(text, style)


def H1(text):
    return Paragraph(text, style_h1)


def H2(text):
    return Paragraph(text, style_h2)


def HR():
    return HRFlowable(width="100%", thickness=0.5, color=BORDER_COLOR, spaceBefore=2*mm, spaceAfter=2*mm)


def make_table(headers, rows, col_widths=None, header_style=style_table_header, cell_styles=None):
    """创建美化的表格。rows中的元素可以是字符串或已构造好的Paragraph/Flowable对象"""
    from reportlab.platypus import Flowable
    header_row = [Paragraph(h, header_style) for h in headers]
    data = [header_row]
    for row in rows:
        processed_row = []
        for c in row:
            if isinstance(c, Flowable):
                # 已经是Flowable对象（如Paragraph），直接使用
                processed_row.append(c)
            else:
                # 字符串，包装为Paragraph
                processed_row.append(Paragraph(str(c), style_table_cell))
        data.append(processed_row)

    if col_widths is None:
        col_widths = [PAGE_W * 0.85 / len(headers)] * len(headers)

    t = Table(data, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), CN_FONT_BOLD),
        ('FONTSIZE', (0, 0), (-1, 0), 8.5),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 8),
        ('TOPPADDING', (0, 0), (-1, 0), 8),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, TABLE_ROW_ALT]),
        ('TOPPADDING', (0, 1), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 1), (-1, -1), 5),
        ('LEFTPADDING', (0, 0), (-1, -1), 6),
        ('RIGHTPADDING', (0, 0), (-1, -1), 6),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]
    t.setStyle(TableStyle(style_cmds))
    return t


def colored_cell(text, color):
    """带颜色的表格单元格"""
    s = ParagraphStyle('colored', fontName=CN_FONT_BOLD, fontSize=8.5, leading=12,
                        textColor=color, alignment=TA_CENTER)
    return Paragraph(text, s)


# ── 数据准备 ──

# 月度总规模
monthly_data = [
    ('2025-01', '45.35', '—', '—'),
    ('2025-02', '44.22', '-1.13', '-2.5%'),
    ('2025-03', '51.78', '+7.56', '+17.1%'),
    ('2025-04', '54.65', '+2.87', '+5.5%'),
    ('2025-05', '48.99', '-5.65', '-10.3%'),
    ('2025-06', '51.17', '+2.18', '+4.5%'),
    ('2025-07', '59.31', '+8.14', '+15.9%'),
    ('2025-08', '55.33', '-3.98', '-6.7%'),
    ('2025-09', '60.92', '+5.58', '+10.1%'),
    ('2025-10', '49.79', '-11.13', '-18.3%'),
    ('2025-11', '60.45', '+10.66', '+21.4%'),
    ('2025-12', '71.91', '+11.46', '+19.0%'),
    ('2026-01', '63.38', '-8.53', '-11.9%'),
    ('2026-02', '44.32', '-19.06', '-30.1%'),
    ('2026-03', '70.23', '+25.91', '+58.5%'),
    ('2026-04', '65.61', '-4.62', '-6.6%'),
    ('2026-05', '53.73', '-11.89', '-18.1%'),
    ('2026-06', '52.00', '-1.72', '-3.2%'),
]

# 增长Top 15
gainers = [
    ('512000', '券商ETF', '214.8', '694.3', '+480', '+223%'),
    ('513180', '恒指科技', '370.4', '752.5', '+382', '+103%'),
    ('159636', '港股通科技30ETF', '75.4', '436.7', '+361', '+479%'),
    ('513010', '港股科技', '119.5', '466.8', '+347', '+291%'),
    ('513130', '恒生科技', '328.8', '667.9', '+339', '+103%'),
    ('159792', '港股通互联网ETF', '317.3', '647.8', '+330', '+104%'),
    ('515880', '通信ETF', '18.1', '282.4', '+264', '+1457%'),
    ('512880', '证券ETF', '246.0', '500.1', '+254', '+103%'),
    ('159740', '恒生科技ETF', '74.8', '291.0', '+216', '+289%'),
    ('159516', '半导体设备ETF', '16.8', '207.8', '+191', '+1135%'),
    ('513380', '科技恒生', '31.4', '197.7', '+166', '+529%'),
    ('159870', '化工ETF', '25.0', '188.9', '+164', '+656%'),
    ('512890', '红利低波', '125.7', '289.0', '+163', '+130%'),
    ('159570', '港股通创新药ETF', '6.9', '156.1', '+149', '+2173%'),
    ('512690', '酒ETF', '202.7', '343.5', '+141', '+69%'),
]

# 下降Top 15
decliners = [
    ('510300', '300ETF', '901.1', '207.4', '-694', '-77%'),
    ('588000', '科创50', '909.3', '310.4', '-599', '-66%'),
    ('510310', 'HS300ETF', '638.6', '138.7', '-500', '-78%'),
    ('510050', '50ETF', '555.6', '64.7', '-491', '-88%'),
    ('588080', '科创板50', '589.4', '188.2', '-401', '-68%'),
    ('159915', '创业板ETF', '439.6', '109.9', '-330', '-75%'),
    ('510330', '华夏300', '398.7', '87.6', '-311', '-78%'),
    ('159919', '沪深300ETF', '380.2', '76.5', '-304', '-80%'),
    ('513060', '恒生医疗', '369.8', '115.6', '-254', '-69%'),
    ('512100', '1000ETF', '224.5', '29.7', '-195', '-87%'),
    ('159949', '创业板50ETF', '316.1', '123.6', '-193', '-61%'),
    ('512480', '半导体', '245.5', '85.8', '-160', '-65%'),
    ('512010', '医药ETF', '642.8', '495.2', '-148', '-23%'),
    ('563800', 'A500龙头', '213.1', '70.8', '-142', '-67%'),
    ('159338', '中证A500ETF', '313.6', '180.0', '-134', '-43%'),
]

# 当前规模Top 20
current_top = [
    ('1', '512170', '医疗ETF', '846.78'),
    ('2', '513180', '恒指科技', '752.55'),
    ('3', '513330', '恒生互联', '739.49'),
    ('4', '512000', '券商ETF', '694.33'),
    ('5', '513130', '恒生科技', '667.92'),
    ('6', '159792', '港股通互联网ETF', '647.83'),
    ('7', '512880', '证券ETF', '500.05'),
    ('8', '512010', '医药ETF', '495.21'),
    ('9', '513010', '港股科技', '466.82'),
    ('10', '159636', '港股通科技30ETF', '436.69'),
    ('11', '512690', '酒ETF', '343.52'),
    ('12', '513050', '中概互联', '335.75'),
    ('13', '588000', '科创50', '310.41'),
    ('14', '159740', '恒生科技ETF', '291.02'),
    ('15', '512890', '红利低波', '289.04'),
    ('16', '515880', '通信ETF', '282.36'),
    ('17', '513980', '科技港股', '240.14'),
    ('18', '159928', '消费ETF', '234.61'),
    ('19', '563360', 'A500基金', '225.92'),
    ('20', '159941', '纳指ETF', '224.81'),
]

# 百亿俱乐部动态
club_data = [
    ('153只', '65只', '32只', '39只'),
]

# 月度百亿+数量
club_monthly = [
    ('2025-02', '54', '109', '1.41'),
    ('2025-04', '55', '109', '1.52'),
    ('2025-06', '53', '104', '1.48'),
    ('2025-08', '59', '108', '1.60'),
    ('2025-10', '64', '119', '1.77'),
    ('2025-12', '67', '125', '1.96'),
    ('2026-02', '72', '136', '1.88'),
    ('2026-04', '69', '128', '1.76'),
    ('2026-06', '65', '126', '1.54'),
]

# 板块数据
sector_data = [
    ('科技', '30', '3,913'),
    ('医药医疗', '35', '3,334'),
    ('港股', '20', '2,238'),
    ('宽基指数', '38', '2,234'),
    ('证券', '13', '1,858'),
    ('红利/价值', '25', '1,453'),
    ('科创板', '20', '1,090'),
    ('消费', '11', '904'),
    ('周期', '16', '858'),
    ('半导体', '13', '836'),
    ('海外', '10', '544'),
    ('新能源', '16', '510'),
    ('创业板', '10', '487'),
    ('港股/中概', '5', '480'),
    ('通信/5G', '3', '325'),
    ('军工', '4', '222'),
    ('金融', '5', '221'),
    ('商品', '8', '332'),
]


# ── 构建PDF ──
def build_pdf():
    output_path = r'D:\claude projects\ETF\ETF基金规模趋势分析报告.pdf'
    doc = SimpleDocTemplate(
        output_path, pagesize=A4,
        leftMargin=18*mm, rightMargin=18*mm,
        topMargin=15*mm, bottomMargin=18*mm,
        title='ETF基金规模趋势分析报告',
        author='ETF分析系统',
        subject='ETF基金规模趋势分析',
    )

    story = []

    # ═══════ 封面/标题 ═══════
    story.append(Spacer(1, 12*mm))
    story.append(P("ETF基金规模趋势分析报告", style_title))
    story.append(P("数据区间：2025年1月2日 — 2026年6月25日  |  覆盖1,557只基金  |  45万+条记录", style_subtitle))
    story.append(HR())

    # 关键指标卡片
    story.append(H2("📊 核心指标"))
    metrics_data = [
        ['2026-06总规模', '期初→期末增长', '百亿+基金', '峰值月份', '基金总数'],
        ['52.00万亿份', '+13.7%', '65只', '2025-12 (71.91万亿)', '1,557只'],
    ]
    t = Table(metrics_data, colWidths=[doc.width/5]*5)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), TABLE_HEADER_BG),
        ('TEXTCOLOR', (0, 0), (-1, 0), white),
        ('FONTNAME', (0, 0), (-1, 0), CN_FONT_BOLD),
        ('FONTNAME', (0, 1), (-1, -1), CN_FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('FONTSIZE', (0, 1), (-1, -1), 13),
        ('FONTNAME', (0, 1), (-1, -1), CN_FONT_BOLD),
        ('TEXTCOLOR', (0, 1), (-1, -1), SECONDARY),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('GRID', (0, 0), (-1, -1), 0.4, BORDER_COLOR),
        ('BACKGROUND', (0, 1), (-1, 1), BG_LIGHT),
    ]))
    story.append(t)
    story.append(Spacer(1, 3*mm))

    # ═══════ 一、整体市场趋势 ═══════
    story.append(H1("一、整体市场规模趋势"))
    story.append(P("ETF市场在2025-2026年期间呈现显著的周期性波动特征。2025年12月达到规模峰值71.91万亿份，"
                    "此后受春节因素影响在2026年2月回落至44.32万亿份，随后3月强劲反弹至70.23万亿份。"
                    "整体来看，市场月均波动幅度在±10-30%之间，显示出资金面的高度活跃性。"))

    # 月度数据表
    m_rows = []
    for row in monthly_data:
        change_cell = row[2]
        if change_cell.startswith('+'):
            change_cell_para = colored_cell(change_cell, ACCENT_GREEN)
        elif change_cell.startswith('-'):
            change_cell_para = colored_cell(change_cell, ACCENT_RED)
        else:
            change_cell_para = Paragraph(change_cell, style_table_cell)
        pct_cell = row[3]
        if pct_cell.startswith('+'):
            pct_para = colored_cell(pct_cell, ACCENT_GREEN)
        elif pct_cell.startswith('-'):
            pct_para = colored_cell(pct_cell, ACCENT_RED)
        else:
            pct_para = Paragraph(pct_cell, style_table_cell)
        m_rows.append([
            Paragraph(row[0], style_table_cell),
            Paragraph(f'<b>{row[1]}</b>', style_table_cell),
            change_cell_para,
            pct_para,
        ])

    col_w = [doc.width*0.22, doc.width*0.26, doc.width*0.26, doc.width*0.26]
    story.append(make_table(
        ['月份', '总规模（万亿份）', '环比变化（万亿）', '环比变化率'],
        m_rows, col_widths=col_w
    ))
    story.append(P("数据来源：基金规模.xlsx，按月汇总全部基金规模", style_caption))

    # 关键发现
    story.append(H2("🔍 关键发现"))
    story.append(P("• <b>强周期性</b>：每年2月为季节性低谷（春节效应），3月资金大规模回流，2026年3月环比暴增58.5%"))
    story.append(P("• <b>2025年整体强势</b>：从45万亿→72万亿，下半年显著放量，11-12月连续大幅增长"))
    story.append(P("• <b>2026年上半年偏弱</b>：6月末52万亿，较2025年底峰值回落28%，市场情绪趋于谨慎"))
    story.append(P("• <b>最大单月波动</b>：2026年2月环比-30.1%（春节前大规模赎回），3月环比+58.5%（节后回补）"))

    # ═══════ 二、百亿俱乐部 ═══════
    story.append(PageBreak())
    story.append(H1("二、百亿俱乐部动态分析"))

    story.append(P("百亿以上规模基金是ETF市场的核心力量。截至2026年6月25日，"
                    "共有65只基金规模超过100亿份，合计规模约1.54万亿份。"))
    # 俱乐部概览
    club_table = [
        ['指标', '数值'],
        ['曾达百亿基金总数', '153只'],
        ['当前百亿基金数量', '65只'],
        ['始终维持在百亿（稳定型）', '33只'],
        ['新晋百亿', '32只'],
        ['跌出百亿', '39只（另有部分已回归）'],
    ]
    club_rows = [[Paragraph(r[0], style_table_cell_left), Paragraph(r[1], style_table_cell)] for r in club_table[1:]]
    story.append(make_table(
        club_table[0],
        club_rows,
        col_widths=[doc.width*0.45, doc.width*0.55]
    ))
    story.append(Spacer(1, 3*mm))

    # 百亿月度变化
    story.append(H2("月度百亿+基金数量趋势"))
    cm_rows = []
    for row in club_monthly:
        cm_rows.append([
            Paragraph(row[0], style_table_cell),
            Paragraph(f'<b>{row[1]}</b>只', style_table_cell),
            Paragraph(row[2] + '只', style_table_cell),
            Paragraph(row[3] + '万亿', style_table_cell),
        ])
    story.append(make_table(
        ['月份', '百亿+基金', '50亿+基金', '百亿+合计规模'],
        cm_rows,
        col_widths=[doc.width*0.25]*4
    ))
    story.append(P("百亿+基金数量从2025年初的54只增至2026年2月峰值72只，随后回落至6月的65只", style_caption))

    # ═══════ 三、增长与下降排行 ═══════
    story.append(PageBreak())
    story.append(H1("三、规模变化排行（2025-01-02 → 2026-06-25）"))

    # 增长榜
    story.append(H2("🔥 规模增长 Top 15"))
    g_rows = []
    for row in gainers:
        g_rows.append([
            Paragraph(row[0], style_table_cell),
            Paragraph(row[1], style_table_cell_left),
            Paragraph(row[2], style_table_cell),
            Paragraph(row[3], style_table_cell),
            colored_cell(row[4], ACCENT_GREEN),
            colored_cell(row[5], ACCENT_GREEN),
        ])
    story.append(make_table(
        ['代码', '基金简称', '期初(亿)', '期末(亿)', '增长(亿)', '增长率'],
        g_rows,
        col_widths=[doc.width*0.12, doc.width*0.24, doc.width*0.16, doc.width*0.16, doc.width*0.16, doc.width*0.16]
    ))
    story.append(Spacer(1, 2*mm))
    story.append(P("• <b>港股科技是最大赢家</b>：恒指科技、港股通科技30ETF、港股科技等占据多席，资金持续涌入港股科技赛道"))
    story.append(P("• <b>中小基金爆发力惊人</b>：通信ETF增长1457%、港股通创新药ETF增长2173%，低基数+风口造就超额增长"))
    story.append(P("• <b>证券板块受益于市场活跃</b>：券商ETF增长480亿份(+223%)，证券ETF增长254亿份(+103%)"))

    # 下降榜
    story.append(H2("⚠️ 规模下降 Top 15"))
    d_rows = []
    for row in decliners:
        d_rows.append([
            Paragraph(row[0], style_table_cell),
            Paragraph(row[1], style_table_cell_left),
            Paragraph(row[2], style_table_cell),
            Paragraph(row[3], style_table_cell),
            colored_cell(row[4], ACCENT_RED),
            colored_cell(row[5], ACCENT_RED),
        ])
    story.append(make_table(
        ['代码', '基金简称', '期初(亿)', '期末(亿)', '变化(亿)', '变化率'],
        d_rows,
        col_widths=[doc.width*0.12, doc.width*0.24, doc.width*0.16, doc.width*0.16, doc.width*0.16, doc.width*0.16]
    ))
    story.append(Spacer(1, 2*mm))
    story.append(P("• <b>传统宽基大规模失血</b>：300ETF (-694亿)、科创50 (-599亿)、HS300ETF (-500亿) 领跌全市场"))
    story.append(P("• <b>50ETF跌幅最大</b>：从611亿骤降至65亿，降幅88%，资金从大盘蓝筹向赛道型ETF迁移"))
    story.append(P("• <b>创业板系列全面承压</b>：创业板ETF(-330亿)、创业板50ETF(-193亿)，成长宽基同样遭遇赎回潮"))

    # ═══════ 四、当前规模排名 ═══════
    story.append(PageBreak())
    story.append(H1("四、当前百亿+基金排名（2026-06-25）"))

    c_rows = []
    for i, row in enumerate(current_top):
        rank_style = style_table_cell
        if i < 3:
            rank_color = ACCENT if i == 0 else (SECONDARY if i == 1 else HexColor('#3ba272'))
            rank_text = f'<font color="{rank_color}"><b>{row[0]}</b></font>'
        else:
            rank_text = row[0]
        c_rows.append([
            Paragraph(rank_text, style_table_cell),
            Paragraph(row[1], style_table_cell),
            Paragraph(row[2], style_table_cell_left),
            Paragraph(f'<b>{row[3]}</b>', style_table_cell),
        ])

    story.append(make_table(
        ['排名', '代码', '基金简称', '规模（亿份）'],
        c_rows,
        col_widths=[doc.width*0.12, doc.width*0.18, doc.width*0.42, doc.width*0.28]
    ))
    story.append(Spacer(1, 2*mm))
    story.append(P("前20基金中，港股科技类占7席（35%），医药医疗类占2席，券商证券类占2席。"
                    "医疗ETF以846.78亿份稳居榜首，恒指科技752.55亿份紧随其后。", style_caption))

    # ═══════ 五、板块分布 ═══════
    story.append(H1("五、板块规模分布（2026-06-25）"))

    s_rows = []
    for row in sector_data:
        s_rows.append([
            Paragraph(row[0], style_table_cell_left),
            Paragraph(row[1], style_table_cell),
            Paragraph(row[2], style_table_cell),
        ])
    story.append(make_table(
        ['板块', '基金数量（只）', '总规模（亿份）'],
        s_rows,
        col_widths=[doc.width*0.40, doc.width*0.28, doc.width*0.32]
    ))
    story.append(Spacer(1, 2*mm))
    story.append(P("科技板块以3,913亿份居首，医药医疗3,334亿份紧随其后。港股+港股/中概合计2,718亿份，"
                    "显示出内地资金对港股的高度偏好。传统宽基指数虽规模仍大，但呈萎缩态势。"))

    # ═══════ 六、核心结论 ═══════
    story.append(PageBreak())
    story.append(H1("六、核心结论与投资启示"))

    conclusions = [
        ("1. 港股科技是最大赢家",
         "恒指科技、港股通互联网、港股科技等在前15中占据7席，全年资金持续涌入。"
         "内地投资者对港股科技龙头的配置需求持续旺盛，这一趋势在2026年仍在延续。"),
        ("2. 传统宽基大撤退",
         "沪深300系列、50ETF、创业板ETF规模腰斩甚至跌超80%。资金偏好从被动指数配置"
         "向赛道型、主题型ETF加速转移，反映出投资者更倾向于主动选择赛道。"),
        ("3. 医药板块韧性最强",
         "医疗ETF以847亿份稳居全市场第一，但内部结构剧烈分化——传统医药ETF（512010）"
         "规模下降23%，而港股创新药ETF增长超2000%，创新药正在取代传统医药成为新主线。"),
        ("4. 中小基金爆发现象显著",
         "通信ETF（+1457%）、创新药ETF（+2173%）、半导体设备ETF（+1135%）等中小规模"
         "基金增长惊人，说明风口赛道+低基数能产生超额回报，但也需警惕高波动风险。"),
        ("5. 季节性规律明确",
         "每年2月（春节）为年度最大资金流出窗口，3月为最大流入窗口。2026年2月→3月的"
         "波动幅度达88.5%（-30.1%→+58.5%），把握季节性节奏是ETF投资的重要参考维度。"),
        ("6. 2026年市场趋于谨慎",
         "2026年上半年整体规模从71.91万亿回落至52万亿，波动中枢下移。红利/价值类ETF"
         "逆势增长，表明市场风险偏好下降，资金向防御性策略倾斜。"),
    ]

    for title, desc in conclusions:
        story.append(H2(title))
        story.append(P(desc))

    # ═══════ 免责声明 ═══════
    story.append(Spacer(1, 8*mm))
    story.append(HR())
    story.append(P("免责声明：本报告基于公开可用的基金规模数据自动生成，仅供参考，不构成投资建议。"
                   "过往表现不代表未来收益。投资有风险，入市需谨慎。", style_footer))
    story.append(P(f"报告生成时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
                   f"数据来源：基金规模.xlsx", style_footer))

    # 构建
    doc.build(story)
    print(f"\nPDF report generated: {output_path}")
    return output_path


if __name__ == '__main__':
    build_pdf()
