import * as React from 'react';

declare module 'react-router-dom' {
  export interface LinkProps extends Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
    to: string;
    replace?: boolean;
    state?: unknown;
    reloadDocument?: boolean;
    preventScrollReset?: boolean;
    relative?: 'route' | 'path';
  }
  
  export const Link: React.ForwardRefExoticComponent<
    LinkProps & React.RefAttributes<HTMLAnchorElement>
  >;
}

