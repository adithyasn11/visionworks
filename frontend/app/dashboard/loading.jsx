// Loading state for the customer dashboard: the shell's own shape (see ShellSkeleton), in the theme's colours.
import React from 'react';
import ShellSkeleton from '../components/ShellSkeleton';

export default function Loading() {
  return <ShellSkeleton label="Loading dashboard" />;
}
