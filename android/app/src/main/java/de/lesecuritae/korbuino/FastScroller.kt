package de.lesecuritae.korbuino

import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectVerticalDragGestures
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.derivedStateOf
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.roundToInt
import kotlinx.coroutines.launch

/** Lists shorter than this are quick to swipe through; the scroller only shows for longer ones. */
private const val MIN_ROWS = 40

/**
 * A draggable scroll handle at the right edge of a long list. While it is dragged, a bubble shows
 * where in the list you are ([label] of the row under the handle: a letter, a retailer, a group).
 *
 * Only the handle takes touches, so swipes and taps on the list itself stay untouched.
 * [headerCount] is the number of list items in front of the [rowCount] rows.
 */
@Composable
fun FastScroller(
    listState: LazyListState,
    rowCount: Int,
    headerCount: Int,
    label: (Int) -> String,
    modifier: Modifier = Modifier,
) {
    if (rowCount < MIN_ROWS) return
    val scope = rememberCoroutineScope()
    val density = LocalDensity.current
    var dragging by remember { mutableStateOf(false) }
    var dragFraction by remember { mutableStateOf(0f) }
    var trackHeight by remember { mutableStateOf(0) }
    val handleHeight = 56.dp
    val handlePx = with(density) { handleHeight.toPx() }
    val travel = (trackHeight - handlePx).coerceAtLeast(1f)

    val listFraction by remember(rowCount, headerCount) {
        derivedStateOf {
            val first = (listState.firstVisibleItemIndex - headerCount).coerceAtLeast(0)
            (first.toFloat() / (rowCount - 1).coerceAtLeast(1)).coerceIn(0f, 1f)
        }
    }
    val fraction = if (dragging) dragFraction else listFraction
    val row = (fraction * (rowCount - 1)).roundToInt().coerceIn(0, rowCount - 1)

    Box(modifier.fillMaxHeight().width(36.dp).onSizeChanged { trackHeight = it.height }) {
        if (dragging) {
            Surface(
                color = MaterialTheme.colorScheme.primary,
                contentColor = MaterialTheme.colorScheme.onPrimary,
                shape = RoundedCornerShape(14.dp),
                shadowElevation = 6.dp,
                modifier = Modifier
                    .align(Alignment.TopEnd)
                    .offset { IntOffset(-with(density) { 48.dp.roundToPx() }, (fraction * travel).roundToInt()) },
            ) {
                Text(
                    label(row),
                    fontSize = 22.sp,
                    fontWeight = FontWeight.Bold,
                    modifier = Modifier.padding(horizontal = 18.dp, vertical = 12.dp),
                )
            }
        }
        Box(
            Modifier
                .align(Alignment.TopEnd)
                .offset { IntOffset(0, (fraction * travel).roundToInt()) }
                .width(36.dp)
                .height(handleHeight)
                .pointerInput(rowCount, headerCount, travel) {
                    detectVerticalDragGestures(
                        onDragStart = { dragging = true; dragFraction = listFraction },
                        onDragEnd = { dragging = false },
                        onDragCancel = { dragging = false },
                    ) { change, amount ->
                        change.consume()
                        dragFraction = (dragFraction + amount / travel).coerceIn(0f, 1f)
                        val target = (dragFraction * (rowCount - 1)).roundToInt()
                        scope.launch { listState.scrollToItem(headerCount + target) }
                    }
                },
        ) {
            Box(
                Modifier
                    .align(Alignment.CenterEnd)
                    .padding(end = 5.dp)
                    .width(if (dragging) 10.dp else 6.dp)
                    .fillMaxHeight()
                    .background(
                        MaterialTheme.colorScheme.primary.copy(alpha = if (dragging) 1f else 0.55f),
                        RoundedCornerShape(5.dp),
                    ),
            )
        }
    }
}
