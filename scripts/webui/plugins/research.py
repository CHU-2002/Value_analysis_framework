"""Company overview and navigation declarations through existing plugin points."""
from urllib.parse import urlencode
from ..core.models import NavItem, PanelSpec, Param
from .companies import companies_dataset


def known_company(ctx, value):
    return any(value in (i.get('ticker'), i.get('dir')) for i in companies_dataset(ctx)[0]['companies'])


def overview(ctx, company=None, **_):
    item = next((i for i in companies_dataset(ctx)[0]['companies']
                 if company in (i.get('ticker'), i.get('dir'))), {})
    code = item.get('ticker') or company
    def link(page):
        return '#' + page + '?' + urlencode({'company': code})
    return {'columns': [{'key':'topic','title':'研究步骤','href_key':'href'},
                        {'key':'state','title':'本地状态'}, {'key':'next','title':'下一步'}],
            'rows': [
                {'topic':'行情与估值', 'href':link('charts'), 'state':'从本地原始仓建图，覆盖不足会明确说明', 'next':'核对K线/估值/分位；需要补历史时进入数据计划'},
                {'topic':'报告', 'href':link('report'), 'state':'正式发布与历史版本分别呈现' if item.get('dir') else '尚无本地报告', 'next':'阅读、查找、比较或生成报告'},
                {'topic':'数据', 'href':link('data'), 'state':'计划完备度按公司/期次/档位计算', 'next':'查看范围、确认采集；采集不等于报告已更新'},
            ], 'company': item.get('display_name') or code}


def contribute(registry):
    registry.panel(PanelSpec(id='research.overview', kind='table', title='公司研究路径',
                            provider=overview, params=(Param('company',source='selection.company'),),
                            description='浏览离线；生成与采集需明确确认。'))
    registry.nav(NavItem(id='research', title='概览', group='公司', order=19,
                        panels=('research.overview',), placement='context',
                        requires=('selection.company',), context_resolver=known_company,
                        actions=({'title':'更新数据','page':'data'}, {'title':'生成报告','page':'agent'}),
                        description='选一次公司，在图表、报告、数据与生成之间连续研究。'))
