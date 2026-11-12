import { Progress } from 'antd';
import { AudioOutlined, BulbOutlined, CloudUploadOutlined, DownloadOutlined, FileTextOutlined, LoadingOutlined, PictureOutlined, VideoCameraOutlined } from '@ant-design/icons';
import type { ApiData } from '../api';

// Presentation only: the original SSE/polling controllers own task lifecycle.
export function editorTaskNotice(toast: ApiData) {
  const initial = String(toast.title || '');
  const task = /上传/.test(initial) ? { title: '上传参考资料', icon: <CloudUploadOutlined /> }
    : /人话|改写/.test(initial) ? { title: '优化演讲稿', icon: <FileTextOutlined /> }
    : /演讲稿|script/i.test(initial) ? { title: '生成演讲稿', icon: <FileTextOutlined /> }
    : /生成.*讲解视频/.test(initial) ? { title: '生成讲解视频', icon: <VideoCameraOutlined /> }
    : /视频/.test(initial) ? { title: '导出讲解视频', icon: <VideoCameraOutlined /> }
    : /音频/.test(initial) ? { title: /导出/.test(initial) ? '导出讲解音频' : '生成讲解音频', icon: <AudioOutlined /> }
    : /导出|PPTX|PDF/i.test(initial) ? { title: '导出演示文稿', icon: <DownloadOutlined /> }
    : /配图|图片/.test(initial) ? { title: '生成配图', icon: <PictureOutlined /> }
    : { title: /优化/.test(initial) ? '优化页面内容' : '任务处理中', icon: <BulbOutlined /> };
  const raw = Number.parseFloat(toast.percent);
  const percent = Number.isFinite(raw) ? Math.round(Math.max(0, Math.min(100, raw))) : null;
  const message = String(toast.message || initial).replace(/\s*[（(]\s*\d+(?:\.\d+)?%\s*[）)]\s*$/, '').trim();
  return {
    icon: <span className="editor-task-emblem" aria-hidden>{task.icon}</span>,
    title: <div className="editor-task-title"><strong>{task.title}</strong><span>{percent == null ? <><LoadingOutlined spin aria-hidden /> 处理中</> : `${percent}%`}</span></div>,
    description: <div className="editor-task-body"><p>{message}</p>{percent == null
      ? <span className="editor-task-wait">任务进行中，完成后会通知你。</span>
      : <Progress aria-label={`${task.title}进度`} percent={percent} strokeColor="var(--lp-primary)" size="small" showInfo={false} />}</div>,
    className: 'editor-task-notification',
    classNames: { title: 'editor-task-heading', description: 'editor-task-description' },
    styles: { root: { padding: 14 }, title: { marginInlineStart: 0, marginBottom: 6 }, description: { marginInlineStart: 0 } },
    role: 'status' as const,
  };
}

export function EditorTaskPanel({ tasks }: { tasks: ApiData[] }) {
  if (!tasks.length) return null;
  return <div className="editor-inline-tasks" aria-label="正在进行的任务">{tasks.map(toast => {
    const notice = editorTaskNotice(toast);
    return <section key={toast.key} className="editor-inline-task" role="status">{notice.icon}<div>{notice.title}{notice.description}</div></section>;
  })}</div>;
}
