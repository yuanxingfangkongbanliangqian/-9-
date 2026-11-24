import json
from datetime import date
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import plotly.express as px
import streamlit as st


CATEGORIES = {
    "支出": ["餐饮", "交通", "购物", "居住", "娱乐", "医疗", "教育", "其他"],
    "收入": ["工资", "奖金", "兼职", "投资", "礼金", "其他"],
}


def supabase_request(method: str, path: str, body: dict | None = None) -> list[dict]:
    config = st.secrets["supabase"]
    url = config["url"].rstrip("/") + "/rest/v1/" + path
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    request = Request(
        url,
        data=payload,
        method=method,
        headers={
            "apikey": config["key"],
            "Authorization": f"Bearer {config['key']}",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        },
    )
    try:
        with urlopen(request, timeout=20) as response:
            response_body = response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase 返回 HTTP {error.code}: {detail}") from error
    except URLError as error:
        raise RuntimeError(f"无法连接 Supabase: {error.reason}") from error
    if not response_body:
        return []
    return json.loads(response_body)


def money(cents: int) -> str:
    return f"¥{cents / 100:,.2f}"


st.set_page_config(page_title="日常记账", page_icon="📒", layout="wide")
st.title("日常记账")

try:
    config = st.secrets["supabase"]
    if not config["url"].startswith("https://") or not config["key"]:
        raise ValueError("Invalid Supabase URL or key")
except (KeyError, FileNotFoundError):
    st.error("还没有配置 Supabase。请按 README.md 设置本机连接信息后重启网站。")
    st.stop()

try:
    transactions = supabase_request(
        "GET",
        "ledger_entries?select=id,kind,amount_cents,category,occurred_on,note"
        "&order=occurred_on.desc,id.desc",
    )
except Exception as error:
    st.error(f"读取云端账目失败：{error}")
    st.stop()

today = date.today()
month_key = today.strftime("%Y-%m")
month_rows = [row for row in transactions if row["occurred_on"].startswith(month_key)]
income = sum(row["amount_cents"] for row in month_rows if row["kind"] == "收入")
expense = sum(row["amount_cents"] for row in month_rows if row["kind"] == "支出")

summary = st.columns(3)
summary[0].metric("本月收入", money(income))
summary[1].metric("本月支出", money(expense))
summary[2].metric("本月结余", money(income - expense))

add_tab, analysis_tab, ledger_tab = st.tabs(["记一笔", "收支分析", "流水明细"])

with add_tab:
    st.subheader("新增记录")
    with st.form("transaction_form", clear_on_submit=True):
        kind = st.radio("收支类型", ["支出", "收入"], horizontal=True)
        amount = st.number_input("金额（元）", min_value=0.01, step=1.00, format="%.2f")
        category = st.selectbox("分类", CATEGORIES[kind])
        occurred_on = st.date_input("日期", value=today)
        note = st.text_input("备注", max_chars=200)
        submitted = st.form_submit_button("保存记录", type="primary")

        if submitted:
            try:
                supabase_request("POST", "ledger_entries", {
                    "kind": kind,
                    "amount_cents": round(amount * 100),
                    "category": category,
                    "occurred_on": occurred_on.isoformat(),
                    "note": note.strip(),
                })
                st.success("已保存到 Supabase")
                st.rerun()
            except Exception as error:
                st.error(f"保存失败：{error}")

with analysis_tab:
    st.subheader("收支分析")
    if not transactions:
        st.info("云端账本还没有记录。")
    else:
        month_options = sorted({row["occurred_on"][:7] for row in transactions}, reverse=True)
        selected_month = st.selectbox("统计月份", month_options)
        selected = [row for row in transactions if row["occurred_on"].startswith(selected_month)]
        expenses = [row for row in selected if row["kind"] == "支出"]
        incomes = [row for row in selected if row["kind"] == "收入"]
        left, right = st.columns(2)
        with left:
            st.markdown("**支出分类**")
            if not expenses:
                st.caption("这个月还没有支出记录。")
            else:
                by_category = {}
                for row in expenses:
                    by_category[row["category"]] = by_category.get(row["category"], 0) + row["amount_cents"]
                st.plotly_chart(
                    px.pie(names=list(by_category), values=[cents / 100 for cents in by_category.values()], hole=0.45),
                    use_container_width=True,
                )
        with right:
            st.markdown("**每日收支趋势**")
            daily = {}
            for row in selected:
                key = (row["occurred_on"], row["kind"])
                daily[key] = daily.get(key, 0) + row["amount_cents"]
            if daily:
                st.plotly_chart(
                    px.bar(
                        x=[key[0] for key in daily],
                        y=[cents / 100 for cents in daily.values()],
                        color=[key[1] for key in daily],
                        barmode="group",
                        labels={"x": "日期", "y": "金额（元）", "color": "类型"},
                    ),
                    use_container_width=True,
                )
        st.caption(
            f"{selected_month}：收入 {money(sum(row['amount_cents'] for row in incomes))}，"
            f"支出 {money(sum(row['amount_cents'] for row in expenses))}"
        )

with ledger_tab:
    st.subheader("流水明细")
    if not transactions:
        st.info("云端账本还没有记录。可以在“记一笔”中添加第一笔。")
    else:
        filters = st.columns(2)
        kind_filter = filters[0].selectbox("收支类型", ["全部", "支出", "收入"])
        month_filter = filters[1].selectbox(
            "月份", ["全部"] + sorted({row["occurred_on"][:7] for row in transactions}, reverse=True)
        )
        visible = [
            row for row in transactions
            if (kind_filter == "全部" or row["kind"] == kind_filter)
            and (month_filter == "全部" or row["occurred_on"].startswith(month_filter))
        ]
        st.markdown("| 编号 | 日期 | 类型 | 分类 | 金额 | 备注 |\n|---:|---|---|---|---:|---|")
        for row in visible:
            st.markdown(
                f"| {row['id']} | {row['occurred_on']} | {row['kind']} | "
                f"{row['category']} | {money(row['amount_cents'])} | {row['note']} |"
            )

        with st.expander("删除一笔记录"):
            delete_id = st.selectbox("选择要删除的记录编号", [row["id"] for row in visible])
            if st.button("删除记录", type="secondary"):
                try:
                    supabase_request("DELETE", f"ledger_entries?id=eq.{delete_id}")
                    st.success("记录已删除")
                    st.rerun()
                except Exception as error:
                    st.error(f"删除失败：{error}")
