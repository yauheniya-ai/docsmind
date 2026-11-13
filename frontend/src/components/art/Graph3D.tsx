import { useEffect, useRef } from 'react';
import ForceGraph3D from '3d-force-graph';

type GraphNode = {
  id: string;
  x?: number;
  y?: number;
  z?: number;
};

type GraphLink = {
  source: string;
  target: string;
};

type GraphData = {
  nodes: GraphNode[];
  links: GraphLink[];
};

const NODE_COUNT = 500;

/*
 * The graph starts deliberately spread out.
 * The force simulation then pulls it into its natural compact shape.
 */
const INITIAL_SPREAD = 420;

function createGraphData(): GraphData {
  const nodes: GraphNode[] = Array.from(
    { length: NODE_COUNT },
    (_, i) => {
      /*
       * Fibonacci-sphere distribution.
       *
       * This gives the new graph a controlled, evenly distributed
       * starting shape instead of letting d3 choose an unpredictable
       * initial placement.
       */
      const phi = Math.acos(
        1 - (2 * (i + 0.5)) / NODE_COUNT
      );

      const theta =
        Math.PI *
        (1 + Math.sqrt(5)) *
        i;

      const radius =
        INITIAL_SPREAD *
        (0.75 + Math.random() * 0.25);

      return {
        id: `node-${i}`,

        x: radius * Math.sin(phi) * Math.cos(theta),
        y: radius * Math.sin(phi) * Math.sin(theta),
        z: radius * Math.cos(phi),
      };
    }
  );

  const links: GraphLink[] = [];

  for (let i = 0; i < NODE_COUNT; i++) {
    const connections = 2 + Math.floor(Math.random() * 3);

    for (let j = 0; j < connections; j++) {
      const target = Math.floor(
        Math.random() * NODE_COUNT
      );

      if (target !== i) {
        links.push({
          source: `node-${i}`,
          target: `node-${target}`,
        });
      }
    }
  }

  return {
    nodes,
    links,
  };
}

export default function Graph3D() {
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const container = containerRef.current;

    if (!container) {
      return;
    }

    // ------------------------------------------------------------
    // Graph
    // ------------------------------------------------------------

    // The published type declarations for 3d-force-graph model its default
    // export as a `new`-able constructor, but the actual runtime API (and
    // its own docs/examples) use a double-call builder:
    // `ForceGraph3D(configOptions)(domElement)`. Cast through `unknown` to
    // call it the way it actually works at runtime.
    type ForceGraph3DFactory = (configOptions?: Record<string, unknown>) => (element: HTMLElement) => any // eslint-disable-line @typescript-eslint/no-explicit-any
    const graph = (ForceGraph3D as unknown as ForceGraph3DFactory)({
      rendererConfig: {
        antialias: true,
        alpha: true,
        preserveDrawingBuffer: false,
      },
      controlType: 'orbit',
    })(container);

    // ------------------------------------------------------------
    // Transparent canvas
    // ------------------------------------------------------------

    graph.backgroundColor('rgba(0, 0, 0, 0)');

    container.style.background = 'transparent';

    const canvas = container.querySelector('canvas');

    if (canvas) {
      canvas.style.background = 'transparent';
    }

    // ------------------------------------------------------------
    // DocsMind visual style
    // ------------------------------------------------------------

    graph
      .nodeColor(() => 'hsl(12 70% 48%)')
      .linkColor(() => 'hsl(215 35% 18%)')
      .linkWidth(1.1)
      .linkOpacity(0.28)
      .linkDirectionalParticles(2)
      .linkDirectionalParticleWidth(1.8)
      .linkDirectionalParticleColor(
        () => 'hsl(12 70% 48%)'
      )
      .nodeRelSize(3)
      .showNavInfo(false);

    // ------------------------------------------------------------
    // Disable user interaction
    // ------------------------------------------------------------

    const controls = graph.controls();

    if (controls) {
      controls.enableRotate = false;
      controls.enableZoom = false;
      controls.enablePan = false;
    }

    // ------------------------------------------------------------
    // Initial graph
    // ------------------------------------------------------------

    graph.graphData(createGraphData());

    // ------------------------------------------------------------
    // Camera
    // ------------------------------------------------------------

    const camera = graph.camera() as {
      fov?: number;
      aspect?: number;
    };

    const getCameraDistance = () => {
      const width = container.clientWidth || 1;
      const height = container.clientHeight || 1;

      const aspect = width / height;

      const verticalFov =
        ((camera.fov ?? 50) * Math.PI) / 180;

      const horizontalFov =
        2 *
        Math.atan(
          Math.tan(verticalFov / 2) * aspect
        );

      /*
       * We need enough distance for the INITIAL_SPREAD
       * sphere to fit completely inside the camera frustum.
       */
      const verticalDistance =
        INITIAL_SPREAD /
        Math.sin(verticalFov / 2);

      const horizontalDistance =
        INITIAL_SPREAD /
        Math.sin(horizontalFov / 2);

      return (
        Math.max(
          verticalDistance,
          horizontalDistance
        ) * 1.25 / 1.5
      );
    };

    let orbitRadius = 900;

    const setInitialCamera = () => {
      if (
        container.clientWidth > 0 &&
        container.clientHeight > 0
      ) {
        orbitRadius = getCameraDistance();
      }

      graph.cameraPosition({
        x: 0,
        y: 80,
        z: orbitRadius,
      });
    };

    setInitialCamera();

    // ------------------------------------------------------------
    // Automatic orbit
    // ------------------------------------------------------------

    const ROTATION_SPEED = 0.4;

    let rotation = 0;
    let lastTime: number | null = null;
    let animationFrameId: number;

    const animate = (time: number) => {
      if (lastTime === null) {
        lastTime = time;
      }

      const deltaSeconds =
        (time - lastTime) / 1000;

      lastTime = time;

      rotation +=
        ROTATION_SPEED * deltaSeconds;

      /*
       * IMPORTANT:
       *
       * orbitRadius does NOT change according to the
       * graph's current bounding box.
       *
       * Therefore the camera stays at a fixed distance
       * while the force simulation moves the nodes.
       *
       * The visible result is:
       *
       *     LARGE GRAPH
       *          ↓
       *     graph contracts
       *          ↓
       *     SMALLER GRAPH
       */
      graph.cameraPosition({
        x: Math.sin(rotation) * orbitRadius,
        y: 80,
        z: Math.cos(rotation) * orbitRadius,
      });

      animationFrameId =
        requestAnimationFrame(animate);
    };

    animationFrameId =
      requestAnimationFrame(animate);

    // ------------------------------------------------------------
    // Reveal
    // ------------------------------------------------------------

    const REVEAL_AFTER_FRAMES = 6;

    let framesWithSize = 0;
    let revealed = false;

    const revealObserver = () => {
      if (revealed) {
        return;
      }

      if (
        container.clientWidth > 0 &&
        container.clientHeight > 0
      ) {
        framesWithSize++;
      }

      if (
        framesWithSize >=
        REVEAL_AFTER_FRAMES
      ) {
        revealed = true;
        container.style.opacity = '1';
      }
    };

    const revealFrame = () => {
      revealObserver();

      if (!revealed) {
        requestAnimationFrame(revealFrame);
      }
    };

    requestAnimationFrame(revealFrame);

    // ------------------------------------------------------------
    // Click → regenerate
    // ------------------------------------------------------------

    const handleClick = () => {
      /*
       * Create a fresh graph whose nodes begin widely
       * distributed.
       *
       * The force simulation then contracts them.
       *
       * We DO NOT change the camera distance here.
       */
      graph.graphData(createGraphData());
    };

    container.addEventListener(
      'click',
      handleClick
    );

    // ------------------------------------------------------------
    // Resize
    // ------------------------------------------------------------

    const resizeObserver =
      new ResizeObserver(() => {
        const width =
          container.clientWidth;

        const height =
          container.clientHeight;

        if (width > 0 && height > 0) {
          graph.width(width);
          graph.height(height);

          /*
           * Recalculate the camera distance only when
           * the actual container size changes.
           *
           * It is NOT recalculated during graph animation.
           */
          orbitRadius =
            getCameraDistance();
        }
      });

    resizeObserver.observe(container);

    // ------------------------------------------------------------
    // Cleanup
    // ------------------------------------------------------------

    return () => {
      cancelAnimationFrame(
        animationFrameId
      );

      resizeObserver.disconnect();

      container.removeEventListener(
        'click',
        handleClick
      );

      while (container.firstChild) {
        container.removeChild(
          container.firstChild
        );
      }
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="w-full h-full"
      style={{
        background: 'transparent',

        opacity: 0,

        transition:
          'opacity 0.5s ease-out',

        WebkitMaskImage:
          'radial-gradient(ellipse at center, black 55%, transparent 100%)',

        maskImage:
          'radial-gradient(ellipse at center, black 55%, transparent 100%)',
      }}
      aria-label="3D document knowledge graph"
    />
  );
}
