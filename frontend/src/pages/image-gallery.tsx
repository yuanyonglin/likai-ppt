import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Card, Checkbox, Empty, Input, Modal, Pagination, Select, Space, Spin, Tabs, Tag } from 'antd';
import { DeleteOutlined, PictureOutlined, ReloadOutlined, UploadOutlined } from '@ant-design/icons';
import { request, type ApiData } from '../api';
import { useAdminActions } from '../components/admin-data';
import { ImageActions, downloadImageFile, fileSize, imageEndpoint, imageSources, sourceLabel } from '../components/images';
import { modalFocus } from '../components/modal-focus';
import ImageUpload from './image-upload';
import ImageDetail from './image-detail';
import { ArtifactLibrary } from '../components/artifact-library';
import { SourceLibrary } from '../components/source-library';
import { ExplainerVideoLibrary } from '../components/explainer-video';

export default function MaterialLibrary() {
  const [active, setActive] = useState(() => { const tab = new URLSearchParams(location.search).get('tab'); return tab === 'exports' || tab === 'sources' || tab === 'explainer' ? tab : 'images'; });
  return <Tabs activeKey={active} onChange={value => { setActive(value); const url = new URL(location.href); if (value !== 'images') url.searchParams.set('tab', value); else url.searchParams.delete('tab'); history.replaceState(history.state, '', url); }} items={[
    { key: 'images', label: '图片素材', children: <ImageGallery /> },
    { key: 'exports', label: '导出文件', children: <ArtifactLibrary active={active === 'exports'} /> },
    { key: 'sources', label: '项目资料', children: <SourceLibrary active={active === 'sources'} /> },
    { key: 'explainer', label: '🎬 讲解视频', children: <ExplainerVideoLibrary active={active === 'explainer'} /> },
  ]} />;
}

function ImageGallery() {
  const [images, setImages] = useState<ApiData[]>([]); const [total, setTotal] = useState(0); const [page, setPage] = useState(1);
  const [category, setCategory] = useState(''); const [search, setSearch] = useState(''); const [query, setQuery] = useState(''); const [sort, setSort] = useState('created_desc');
  const [loading, setLoading] = useState(true); const [error, setError] = useState('');
  const [batch, setBatch] = useState(false); const [selected, setSelected] = useState<string[]>([]);
  const [upload, setUpload] = useState(false); const [detail, setDetail] = useState<string>(); const [clearing, setClearing] = useState(0); const [downloading, setDownloading] = useState(false);
  const read = useRef<AbortController | null>(null); const latest = useRef({ page: 1, category: '', search: '', sort: 'created_desc' }); const actions = useAdminActions();
  async function load(target = latest.current) {
    read.current?.abort(); const controller = new AbortController(); read.current = controller; latest.current = target; setLoading(true); setError('');
    try {
      const params = new URLSearchParams({ ...target, page: String(target.page), per_page: '20' });
      const result = await request('/api/image/gallery/list?' + params, 'GET', undefined, controller.signal);
      if (controller.signal.aborted) return;
      const count = Number(result.pagination?.total_count || 0); const last = Math.max(1, Math.ceil(count / 20));
      if (target.page > last) { await load({ ...target, page: last }); return; }
      setImages(result.images || []); setTotal(count); setPage(target.page);
    } catch (reason) { if (!controller.signal.aborted) setError((reason as Error).message); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  useEffect(() => { const timer = setTimeout(() => setQuery(search), 200); return () => clearTimeout(timer); }, [search]);
  useEffect(() => { load({ page: 1, category, search: query, sort }); return () => read.current?.abort(); }, [category, query, sort]);
  const busy = actions.pending || downloading;
  function toggle(id: string, checked: boolean) { setSelected(values => checked ? Array.from(new Set([...values, id])) : values.filter(value => value !== id)); }
  function deleteImages(ids: string[], single = false) {
    const snapshot = [...ids];
    actions.confirm(single ? '删除图片' : `删除 ${snapshot.length} 张图片`, '此操作不可撤销。确认删除这些图片？', async () => {
      const result = await actions.run(single ? imageEndpoint('delete', snapshot[0]) : '/api/image/gallery/batch-delete', single ? 'DELETE' : 'POST', single ? undefined : { image_ids: snapshot });
      const failed: string[] = result.failed_ids || []; const removed = snapshot.filter(id => !failed.includes(id));
      setSelected(values => values.filter(id => !removed.includes(id)));
      if (detail && removed.includes(detail)) setDetail(undefined);
      if (failed.length) actions.message.warning(`已删除 ${removed.length} 张，${failed.length} 张未删除，请重试。`);
      else actions.message.success(single ? '图片已删除' : `已删除 ${removed.length} 张图片`);
      await load();
    }, true);
  }
  return <div className="image-gallery-page settings-stack" onKeyDown={event => {
    if (!batch || busy || upload || detail || clearing) return;
    const target = event.target as HTMLElement;
    if (target.matches('input,textarea,select') || target.isContentEditable || target.closest('[role="dialog"]')) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
      event.preventDefault(); if (!loading) setSelected(values => Array.from(new Set([...values, ...images.map(image => image.image_id)])));
    } else if (event.key === 'Delete' && selected.length) { event.preventDefault(); deleteImages(selected); }
  }}>
    <div className="page-heading"><h1>本地图床</h1><Space wrap>
      <Button href="/image-generation-test" icon={<PictureOutlined aria-hidden />}>AI 生成</Button><Button aria-pressed={batch} disabled={busy} onClick={() => { setBatch(!batch); setSelected([]); }}>批量选择</Button>
      <Button id="upload-trigger" type="primary" icon={<UploadOutlined aria-hidden />} onClick={() => setUpload(true)}>上传图片</Button>
    </Space></div>
    <Card size="small"><div className="image-toolbar">
      <Select aria-label="图片分类" value={category} options={[{ value: '', label: '全部分类' }, ...imageSources]} onChange={setCategory} />
      <Input.Search aria-label="搜索图片" placeholder="搜索标题、描述或标签" allowClear value={search} onChange={event => setSearch(event.target.value)} onSearch={setQuery} />
      <Select aria-label="图片排序" value={sort} options={[['created_desc', '最新创建'], ['created_asc', '最早创建'], ['accessed_desc', '最近访问'], ['size_desc', '文件从大到小'], ['size_asc', '文件从小到大']].map(([value, label]) => ({ value, label }))} onChange={setSort} />
      <Button aria-label="刷新图库" icon={<ReloadOutlined />} loading={loading} onClick={() => load()} />
    </div></Card>
    {batch && <Card size="small"><div className="image-batch-toolbar"><span role="status">已选择 {selected.length} 张{selected.some(id => !images.some(image => image.image_id === id)) && '（包含其他页）'}</span><Space wrap>
      <Button aria-keyshortcuts="Control+A Meta+A" disabled={busy || loading || !images.length} onClick={() => setSelected(values => Array.from(new Set([...values, ...images.map(image => image.image_id)])))}>选择本页</Button>
      <Button disabled={busy || !selected.length} onClick={() => setSelected([])}>清除选择</Button>
      <Button disabled={busy || !selected.length} loading={downloading} onClick={async () => {
        const snapshot = [...selected]; if (downloading) return; setDownloading(true);
        try { await downloadImageFile('/api/image/gallery/batch-download', { image_ids: snapshot }); } catch (reason) { actions.message.error((reason as Error).message); } finally { setDownloading(false); }
      }}>下载选中</Button>
      <Button aria-keyshortcuts="Delete" danger disabled={busy || !selected.length} onClick={() => deleteImages(selected)}>删除选中</Button>
      <Button danger disabled={busy || loading || !total} onClick={() => { actions.clearError(); setClearing(1); }}>清空我的图库</Button>
    </Space></div></Card>}
    {error && <Alert type="error" title="图库加载失败" description={error} action={<Button onClick={() => load()}>重试</Button>} />}
    {loading && <div role="status" className="image-loading"><Spin size="small" /> 正在加载图片…</div>}
    <section className="image-grid" aria-label="图片素材" aria-busy={loading}>{images.map(image => <Card size="small" key={image.image_id} className={`image-card ${selected.includes(image.image_id) ? 'image-card-selected' : ''}`}>
      {batch && <Checkbox className="image-card-check" aria-label={`选择 ${image.title || image.filename}`} checked={selected.includes(image.image_id)} disabled={busy} onChange={event => toggle(image.image_id, event.target.checked)} />}
      <button className="image-card-preview" aria-label={`查看图片：${image.title || image.filename}`} onClick={() => setDetail(image.image_id)}><img src={imageEndpoint('thumbnail', image.image_id)} alt={image.title || image.filename} loading="lazy" onError={event => { event.currentTarget.onerror = null; event.currentTarget.src = '/static/images/placeholder.svg'; }} /></button>
      <div className="image-card-body"><h2 title={image.title || image.filename}>{image.title || image.filename}</h2><div className="image-card-meta"><Tag>{sourceLabel(image.source_type)}</Tag><span>{fileSize(image.file_size)}</span></div>
        <div className="image-card-actions"><ImageActions url={imageEndpoint('view', image.image_id)} downloadURL={imageEndpoint('download', image.image_id)} disabled={busy} /><Button danger aria-label={`删除 ${image.title || image.filename}`} icon={<DeleteOutlined />} disabled={busy} onClick={() => deleteImages([image.image_id], true)} /></div>
      </div>
    </Card>)}</section>
    {!loading && !error && !images.length && <Card size="small"><Empty description={query || category ? '没有匹配的图片，试试其他筛选条件。' : '暂无图片，上传图片建立素材库。'}>{!query && !category && <Button type="primary" onClick={() => setUpload(true)}>上传图片</Button>}</Empty></Card>}
    <Space className="projects-pagination"><span className="subtle">共 {total} 张</span><Pagination current={page} total={total} pageSize={20} showSizeChanger={false} showLessItems disabled={loading} onChange={page => load({ ...latest.current, page })} /></Space>
    <ImageUpload open={upload} onClose={() => setUpload(false)} onUploaded={() => load({ ...latest.current, page: 1 })} />
    <ImageDetail id={detail} onClose={() => setDetail(undefined)} onSaved={() => load()} onDelete={id => deleteImages([id], true)} />
    <Modal centered open={clearing > 0} title={clearing === 1 ? '清空我的图库' : '再次确认清空图库'} okText={clearing === 1 ? '继续' : '确认清空'} cancelText="取消" okButtonProps={{ danger: true }} confirmLoading={actions.pending} closable={!actions.pending} mask={{ closable: !actions.pending }} keyboard={!actions.pending} modalRender={modalFocus} onCancel={() => setClearing(0)} onOk={async () => {
      if (clearing === 1) { setClearing(2); return; }
      try { const result = await actions.run('/api/image/gallery/clear-all', 'POST'); setSelected([]); setBatch(false); setClearing(0); actions.message.success(result.message || '图库已清空'); await load(); } catch { /* Keep confirmation open for retry. */ }
    }}><p>将删除你图库中的所有图片，删除后无法恢复。</p>{actions.error && <Alert type="error" title={actions.error} />}</Modal>
  </div>;
}
