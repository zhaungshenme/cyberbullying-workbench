import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '网暴行为溯源追踪工作台',
  description: '提交原始文本，通过服务端工作流获取可复核的文本观察。',
  icons: { icon: '/workbench.svg' },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>;
}
