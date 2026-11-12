// Loading state for the organisation settings screen (it had none, so the click showed nothing until the
// server answered): the dashboard shell with the settings pages' centred column.
import React from 'react';
import ShellSkeleton from '../../components/ShellSkeleton';

export default function Loading() {
  return <ShellSkeleton label="Loading organisation settings" narrow panels={3} />;
}
