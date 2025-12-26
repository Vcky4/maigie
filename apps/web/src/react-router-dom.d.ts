import * as React from 'react';

declare module 'react-router-dom' {
  export interface LinkProps extends Omit<React.AnchorHTMLAttributes<HTMLAnchorElement>, 'href'> {
    to: string;
    replace?: boolean;
    state?: unknown;
    reloadDocument?: boolean;
    preventScrollReset?: boolean;
    relative?: 'route' | 'path';
    children?: React.ReactNode;
  }
  
  export const Link: React.ForwardRefExoticComponent<
    LinkProps & React.RefAttributes<HTMLAnchorElement>
  >;

  export interface RouteProps {
    path?: string;
    index?: boolean;
    element?: React.ReactElement | null;
    children?: React.ReactNode;
  }

  export const Route: React.ComponentType<RouteProps>;
  
  export interface RoutesProps {
    children?: React.ReactNode;
  }

  export const Routes: React.ComponentType<RoutesProps>;
}

