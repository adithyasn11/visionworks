// Loading state for the members screen: the dashboard shell with the settings pages' centred column, so the
// page fills in where it will appear (the old skeleton had no sidebar and the whole page shifted on arrival).
import React from 'react';
import ShellSkeleton from '../../components/ShellSkeleton';

export default function Loading() {
  return <ShellSkeleton label="Loading members" narrow panels={2} />;
}
