import { Avatar } from 'antd';
import { ArrowRightOutlined, ArrowUpOutlined } from '@ant-design/icons';
import type { ApiData } from '../api';
import { safeWebURL } from '../forms';

export default function HomeSponsors({ sponsors }: { sponsors: ApiData[] }) {
  if (!sponsors.length) return null;
  return <section className="home-sponsors home-wrap" aria-labelledby="homeSponsorsTitle">
    <div className="home-sponsors-intro"><h2 id="homeSponsorsTitle">感谢每一份支持</h2><p>和支持 立铠 PPT 的朋友们，<br />一起让好想法走得更远。</p><a className="home-text-link" href="/sponsors">查看全部赞助 <ArrowRightOutlined aria-hidden /></a></div>
    <ul className="home-sponsor-list" aria-label="首页赞助名单">{sponsors.slice(0, 6).map((sponsor, index) => {
      const nickname = String(sponsor.nickname || '支持者');
      const link = safeWebURL(sponsor.link_url);
      return <li key={index}><Avatar size={44} src={safeWebURL(sponsor.avatar_url)}>{nickname.slice(0, 1)}</Avatar><div className="home-sponsor-copy"><h3 title={nickname}>{nickname}</h3><p>{sponsor.bio || '感谢支持 立铠 PPT 的持续迭代与维护。'}</p></div>{link && <a className="home-sponsor-link" href={link} target="_blank" rel="noopener noreferrer" aria-label={`访问 ${nickname} 的主页`}><ArrowUpOutlined rotate={45} aria-hidden /></a>}</li>;
    })}</ul>
  </section>;
}
