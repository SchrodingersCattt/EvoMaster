"use client";

const size = (s: number) => ({ width: s, height: s });

export function SendIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="m22 2-7 20-4-9-9-4Z" />
      <path d="M22 2 11 13" />
    </svg>
  );
}

export function SquareIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <rect width="18" height="18" x="3" y="3" rx="2" />
    </svg>
  );
}

export function Loader2Icon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="animate-spin" {...size(s)} {...rest}>
      <path d="M21 12a9 9 0 1 1-6.219-8.56" />
    </svg>
  );
}

export function BotIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M12 8V4H8" />
      <rect width="16" height="12" x="4" y="8" rx="2" />
      <path d="M2 14h2" />
      <path d="M20 14h2" />
      <path d="M15 13v2" />
      <path d="M9 13v2" />
    </svg>
  );
}

export function WifiIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M12 20h.01" />
      <path d="M8 16.429a5 5 0 0 1 8 0" />
      <path d="M5 12.859a10 10 0 0 1 14 0" />
      <path d="M2 9.214a15 15 0 0 1 20 0" />
    </svg>
  );
}

export function WifiOffIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M12 20h.01" />
      <path d="M8.5 16.429a5 5 0 0 1 7 0" />
      <path d="M5 12.859a10 10 0 0 1 5.17-2.69" />
      <path d="M19 12.859a10 10 0 0 0-2.007-1.523" />
      <path d="M2 9.214a15 15 0 0 1 4.552-2.358" />
      <path d="M22 9.214a15 15 0 0 0-11.285-2.841" />
      <path d="m2 2 20 20" />
    </svg>
  );
}

export function ChevronDownIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="m6 9 6 6 6-6" />
    </svg>
  );
}

export function ChevronRightIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="m9 18 6-6-6-6" />
    </svg>
  );
}

export function FileCodeIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" />
      <polyline points="14 2 14 8 20 8" />
      <path d="m10 13-2 2 2 2" />
      <path d="m14 17 2-2-2-2" />
    </svg>
  );
}

export function ActivityIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M22 12h-4l-3 9L9 3l-3 9H2" />
    </svg>
  );
}

export function ListOrderedIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <line x1="10" x2="21" y1="6" y2="6" />
      <line x1="10" x2="21" y1="12" y2="12" />
      <line x1="10" x2="21" y1="18" y2="18" />
      <path d="M4 6h1v4" />
      <path d="M4 10h2" />
      <path d="M6 18H4c0-1 2-2 2-3s-1-1.5-2-1" />
    </svg>
  );
}

export function CircleDotIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="2" fill="currentColor" />
    </svg>
  );
}

export function CircleCheckIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <circle cx="12" cy="12" r="10" />
      <path d="m9 12 2 2 4-4" />
    </svg>
  );
}

export function CircleXIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <circle cx="12" cy="12" r="10" />
      <path d="m15 9-6 6" />
      <path d="m9 9 6 6" />
    </svg>
  );
}

export function RefreshCwIcon(props: React.SVGProps<SVGSVGElement> & { size?: number }) {
  const { size: s = 16, ...rest } = props;
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...size(s)} {...rest}>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
    </svg>
  );
}
