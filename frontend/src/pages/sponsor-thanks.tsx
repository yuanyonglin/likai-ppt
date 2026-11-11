import { Alert, Avatar, Button, Card, Empty, Tag } from 'antd';
import { ArrowUpOutlined, HeartOutlined } from '@ant-design/icons';
import type { ApiData } from '../api';
import { safeWebURL } from '../forms';

export default function SponsorThanks({ data }: { data: ApiData }) {
  const sponsors: ApiData[] = data.sponsors || [];
  const preview = data.previewMode && data.user?.is_admin;
  return <div className="sponsor-thanks-page settings-stack">
    <header className="sponsor-thanks-intro">
      <div className="sponsor-thanks-title"><div><span className="sponsor-thanks-eyebrow">每一份支持，都在推动创作</span><h1>赞助感谢</h1><p>感谢每一位支持 立铠 PPT 持续迭代与维护的人。</p></div><span className="sponsor-thanks-emblem" aria-hidden><HeartOutlined /></span></div>
      <div className="sponsor-thanks-summary"><span><strong>{sponsors.length}</strong> 位支持者</span><span>让好想法，走得更远。</span></div>
    </header>
    {preview && <Alert type="warning" showIcon title="管理员预览：当前赞助感谢页尚未公开。" description="在运营设置中开启并保存“公开赞助感谢页”，访客即可从页脚进入。" action={<Button href="/admin/community#sponsors">前往设置</Button>} />}
    {sponsors.length ? <section className="sponsor-thanks-grid" aria-label="赞助名单">{sponsors.map((sponsor, index) => <Card size="small" key={index} className="sponsor-thanks-card">
      <div className="community-sponsor-identity"><Avatar size={48} src={safeWebURL(sponsor.avatar_url)}>{String(sponsor.nickname).slice(0, 1)}</Avatar><div><h2>{sponsor.nickname}</h2>{sponsor.amount && <span className="sponsor-thanks-amount">{sponsor.amount}</span>}</div></div>
      <p className="sponsor-thanks-bio">{sponsor.bio || '感谢你的支持。'}</p>
      {(sponsor.note || safeWebURL(sponsor.link_url)) && <div className="sponsor-thanks-card-footer">{sponsor.note && <Tag>{sponsor.note}</Tag>}{safeWebURL(sponsor.link_url) && <a className="sponsor-thanks-link" href={safeWebURL(sponsor.link_url)} target="_blank" rel="noopener noreferrer">访问主页 <ArrowUpOutlined rotate={45} aria-hidden /></a>}</div>}
    </Card>)}</section> : <Card size="small" className="sponsor-thanks-empty"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={preview ? '暂无可展示的赞助信息，请在运营设置中添加并允许展示。' : '赞助感谢页已开启，暂无赞助信息。'} /></Card>}
  </div>;
}
